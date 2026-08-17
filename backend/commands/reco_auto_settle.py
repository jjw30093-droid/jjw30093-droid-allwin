"""「每日精选」推荐单自动结算编排层(P2.C,依赖上两阶段的地基):

- 赔率合约:backend/commands/reco_odds_contract.py + backend/migrations/platform/
  0014_reco_odds_contract.sql(entry_type、canonical_decimal_odds、
  reco_slips.settle_source)。
- 单腿结算判定纯函数:backend/commands/reco_settlement_math.py::resolve_leg_result
  (数据不足或市场/符号约定未证实时抛 SettlementUnresolvable,不猜测)。
- 实际写结算:backend/commands/reco.py::settle_slip(乘法、result 汇总、
  audit_logs 均已实现)。

本模块只做"串起来"这一件事,不重新实现任何结算算术或赛前信息判定,
也不重新定义"正式完赛"的判据。

安全边界(CLAUDE.md:宁可 fail-closed,不猜一个可能算错的结果):

1. 只处理 entry_type='provenance_bound' 且 match_id 非空的腿——legacy_manual
   腿(缺乏真实溯源的手写内容)永远不会被本任务自动判定。
2. 一场比赛"正式完赛"的唯一判据:dim_match.status='Finish' 且
   home_score/away_score 均非 NULL(与上一阶段查询规则一致)。不满足的腿本轮
   跳过,不报错、不猜测,原因写进 skip_reasons(可追溯到具体 slip_id/leg_id/
   match_id,不是笼统的一个数字)。
3. 一张 slip 的全部腿必须一起处理,不允许"这批结算一半、下批再结算另一半":
   - settle_source='manual' 的单永远不覆盖(人工已经结算过,自动任务不覆盖);
   - 只要这张单里混有任何 legacy_manual 腿,整单本轮跳过、留给人工——不做
     "混合单里能判定的腿先自动结算、剩下的等人工"这种半自动半人工的拆分;
   - 只要还有一条 provenance_bound 腿暂不可判定(比赛未完赛/角球等统计数据
     缺失/resolve_leg_result 报 SettlementUnresolvable),整单本轮都不结算——
     不允许"部分腿判定了、部分腿又因为凑不齐结果作废"这种数据丢失路径。
4. 判定(纯内存计算,不查任何写操作)与 settle_slip() 写入包在同一个
   tx(conn_platform) 事务里,一张单的全部腿判定与状态更新原子生效。
5. 幂等:候选查询天然只挑 reco_legs.result IS NULL 的腿——已经结算过的腿
   不会被再次选中,重复触发本任务(worker 重跑/进程重启)不会重复结算或
   产生重复 audit_logs。
"""

from __future__ import annotations

import json
import sqlite3

from backend.db.connections import tx

from .reco import settle_slip
from .reco_settlement_math import SettlementUnresolvable, resolve_leg_result


def _match_facts(conn_core: sqlite3.Connection, match_id: int, *, need_corners: bool) -> dict | None:
    """dim_match 一行 + (仅 corners_ou 腿、且已完赛时)双方角球统计。

    角球取数方式与 backend/queries/match_report.py::_team_stats 一致
    (fact_team_match_stats, Period='All'),不重新发明查询口径。
    比赛尚未完赛时不查角球表——没有意义,也避免对未完赛比赛做多余查询。
    """
    row = conn_core.execute(
        "SELECT status, home_score, away_score, Home_Team_ID, Away_Team_ID"
        " FROM dim_match WHERE Match_ID=?",
        (match_id,),
    ).fetchone()
    if row is None:
        return None
    facts = {
        "status": row["status"],
        "home_score": row["home_score"],
        "away_score": row["away_score"],
        "home_corners": None,
        "away_corners": None,
    }
    finished = (
        facts["status"] == "Finish"
        and facts["home_score"] is not None
        and facts["away_score"] is not None
    )
    if need_corners and finished:
        home_id, away_id = row["Home_Team_ID"], row["Away_Team_ID"]
        for s in conn_core.execute(
            "SELECT Team_ID, extra_json FROM fact_team_match_stats"
            " WHERE Match_ID=? AND Period='All'",
            (match_id,),
        ).fetchall():
            try:
                extra = json.loads(s["extra_json"] or "{}")
            except ValueError:
                extra = {}
            corners = extra.get("corners")
            corners = float(corners) if corners is not None else None
            if s["Team_ID"] == home_id:
                facts["home_corners"] = corners
            elif s["Team_ID"] == away_id:
                facts["away_corners"] = corners
    return facts


def _resolve_slip_legs(conn_core: sqlite3.Connection, legs: list) -> tuple[dict[str, str] | None, list[str]]:
    """给一张 slip 的全部腿逐条判定。

    任一腿不可判定 -> 返回 (None, [可追溯的原因摘要]),整单本轮不结算。
    全部可判定 -> 返回 ({leg_id: result}, [])。
    """
    leg_results: dict[str, str] = {}
    for leg in legs:
        if leg["result"] is not None:
            # published(未结算)单的腿理论上全部 result IS NULL——settle_slip
            # 是唯一写 result 的路径,且要求一次性提供全部腿结果。真出现这种
            # 情况说明内部状态不一致,fail-closed 交给人工排查,不猜测复用旧值。
            return None, [
                f"leg_id={leg['id']}: 该腿已有 result 但所属单未结算,内部状态"
                " 不一致,不自动处理"
            ]
        if leg["match_id"] is None:
            return None, [f"leg_id={leg['id']}: 缺少 match_id,无法判定"]
        facts = _match_facts(
            conn_core, leg["match_id"], need_corners=(leg["market"] == "corners_ou")
        )
        if facts is None:
            return None, [
                f"leg_id={leg['id']} match_id={leg['match_id']}: dim_match 无此比赛,无法判定"
            ]
        finished = (
            facts["status"] == "Finish"
            and facts["home_score"] is not None
            and facts["away_score"] is not None
        )
        if not finished:
            return None, [
                f"leg_id={leg['id']} match_id={leg['match_id']}: status="
                f"{facts['status']!r}, 比分 home={facts['home_score']!r}/"
                f"away={facts['away_score']!r},未满足正式完赛条件"
                "(需 status='Finish' 且比分非空),本轮跳过"
            ]
        try:
            result = resolve_leg_result(
                leg["market"], leg["line"], leg["side"],
                home_score=facts["home_score"], away_score=facts["away_score"],
                home_corners=facts["home_corners"], away_corners=facts["away_corners"],
            )
        except SettlementUnresolvable as exc:
            return None, [
                f"leg_id={leg['id']} match_id={leg['match_id']}: 不可自动判定({exc})"
            ]
        leg_results[leg["id"]] = result
    return leg_results, []


def run_auto_settle(
    conn_platform: sqlite3.Connection,
    conn_core: sqlite3.Connection,
    *,
    actor: str = "system",
) -> dict:
    """扫描 published 单里全部可判定的 provenance_bound 腿并整单自动结算。

    conn_platform:reco_slips/reco_legs/audit_logs 所在的可写连接(调用方负责
    生命周期,本函数内部对每张单各开一个短事务)。
    conn_core:dim_match/fact_team_match_stats 只读查询(传只读连接即可,
    本函数不写 core)。
    actor:写入 audit_logs.actor_type 的值(CHECK 只接受 admin/system/user,
    默认 'system')。audit_logs.actor_user_id 恒为 NULL——它是 users(id) 的
    真实外键,自动任务没有对应的真实用户可归因。

    返回统计摘要(供 job_runs.output_count/meta 使用):
    {"settled_slips": int, "skipped_legs": int,
     "skip_reasons": [str, ...], "errors": [str, ...]}
    """
    candidate_legs = conn_platform.execute(
        """SELECT l.id, l.slip_id FROM reco_legs l
           JOIN reco_slips s ON s.id = l.slip_id
           WHERE l.entry_type='provenance_bound' AND l.match_id IS NOT NULL
             AND s.status='published' AND l.result IS NULL
           ORDER BY l.slip_id, l.sort_order"""
    ).fetchall()

    slip_ids: list[str] = []
    seen: set[str] = set()
    for row in candidate_legs:
        if row["slip_id"] not in seen:
            seen.add(row["slip_id"])
            slip_ids.append(row["slip_id"])

    settled_slips = 0
    settled_leg_ids: set[str] = set()
    skip_reasons: list[str] = []
    errors: list[str] = []

    for slip_id in slip_ids:
        slip = conn_platform.execute(
            "SELECT id, status, settle_source FROM reco_slips WHERE id=?", (slip_id,)
        ).fetchone()
        if slip is None or slip["status"] != "published":
            skip_reasons.append(f"slip_id={slip_id}: 单不存在或状态已变化,本轮跳过")
            continue
        if slip["settle_source"] == "manual":
            skip_reasons.append(
                f"slip_id={slip_id}: 已被人工结算(settle_source=manual),自动任务不覆盖"
            )
            continue

        legs = conn_platform.execute(
            "SELECT id, match_id, market, line, side, entry_type, result"
            " FROM reco_legs WHERE slip_id=? ORDER BY sort_order",
            (slip_id,),
        ).fetchall()

        mixed = [l["id"] for l in legs if l["entry_type"] != "provenance_bound"]
        if mixed:
            skip_reasons.append(
                f"slip_id={slip_id}: 混有 legacy_manual 腿({len(mixed)} 条),"
                " 自动任务不处理混合单,留给人工结算"
            )
            continue

        leg_results, reasons = _resolve_slip_legs(conn_core, legs)
        if leg_results is None:
            skip_reasons.extend(f"slip_id={slip_id} {r}" for r in reasons)
            continue

        try:
            with tx(conn_platform):
                settle_slip(conn_platform, slip_id, leg_results, actor=None, actor_type=actor)
                conn_platform.execute(
                    "UPDATE reco_slips SET settle_source='auto' WHERE id=?", (slip_id,)
                )
        except Exception as exc:  # noqa: BLE001 — 单张单失败不拖垮整轮,如实记录继续
            errors.append(f"slip_id={slip_id}: {type(exc).__name__}: {exc}")
            continue

        settled_slips += 1
        settled_leg_ids.update(leg_results.keys())

    skipped_legs = sum(1 for row in candidate_legs if row["id"] not in settled_leg_ids)

    return {
        "settled_slips": settled_slips,
        "skipped_legs": skipped_legs,
        "skip_reasons": skip_reasons,
        "errors": errors,
    }
