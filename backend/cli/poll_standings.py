"""联赛积分榜(fact_league_table)迟到刷新:按"最近一场完赛比赛+6h"到期判断
调用 ingest_season_tables() 重新拉取。

纯判断逻辑在 backend/ingest/standings_refresh_poll.py(due_refresh,不做
I/O);本文件是唯一的调用方——负责发现每个候选联赛当前赛季、算出"最近一场
完赛比赛的开球时间",判断是否到期,到期则调用 ingest_season_tables() 触发
真正的整季重抓(DELETE+INSERT,幂等),并落库 standings_refresh_state。

范围与节奏(CLAUDE.md §6.3/§13,站长最终决定,不在此重新推导):
- 遍历 backend/queries/leagues.py::LEAGUE_META 的**全部**联赛(单一真源,
  不再另维护一份联赛白名单——2026-08-26 起,此前刻意收窄成只有英超);
- "赛季"取该联赛当前 dim_match 里最新一场完赛比赛(status='Finish')自身
  的 Season 列——dim_match.Season 已经由 migration 0011 的触发器保证与
  (League_ID, Date) 推导一致,直接读它比重新调用 season_for_match()/
  resolve_current_season() 更直接,也天然避免了"当前赛季"和"最近一场完赛
  比赛所属赛季"在赛季交替边界上可能不一致的问题;
- 最近一场完赛比赛的 kickoff_at_utc + 6 小时到期,到期即调用一次
  ingest_season_tables(),不做检查点式的有限次重试(与 physical_stats_poll
  不同构,那是"有效值检查"语义,这里是纯粹的时间到期语义);
- **每轮最多真正刷新 MAX_REFRESHES_PER_RUN(5)个联赛**——12 个联赛可能在
  首轮同时到期,全刷一遍代价太高。两阶段执行:阶段一对全部联赛做廉价的
  纯 DB 到期判断,阶段二只对"到期候选里 due_at 最旧的 5 个"发起真正的
  HTTP+DELETE/INSERT 刷新,其余到期但被挤掉的记 action="deferred"、
  不写 state,下一轮因为最旧优先必然被继续排到前面,不会被持续饿死;
- 状态表 standings_refresh_state 落在 core(allwin.db)——理由见
  backend/migrations/core/0014_standings_refresh_state.sql 头注释。

用法:
  .venv/bin/python -m backend.cli.poll_standings --due
  .venv/bin/python -m backend.cli.poll_standings --due --now 2026-08-26T10:00:00Z

⚠️ ingest_season_tables() 会往 stdout 打印一行落库进度,刷新多个联赛时
本命令的输出不再是单行纯 JSON(摘要仍是最后一行)——脚本化消费请用
`... | tail -1 | jq` 而不是直接 `| jq`。
"""

import argparse
import json
import os
import sys

# ingest_league.py 是 script 风格模块(内部 `from db import ...`),不可直接
# 包导入 ingest_season_tables;沿用 backend/cli/backfill_season_tables.py
# 头部同款 sys.path 桥接,不复制其函数体。
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_BACKEND_DIR, os.path.join(_BACKEND_DIR, "ingest")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.db.connections import connect_rw, tx
from backend.db.util import utc_now_iso
from backend.ingest.standings_refresh_poll import (
    MAX_REFRESHES_PER_RUN,
    due_refresh,
)
from backend.queries.leagues import LEAGUE_META


def _latest_finished_match(conn, league_id: int):
    """该联赛当前已知最新一场完赛比赛的 (season, kickoff_at_utc);
    没有任何完赛比赛时返回 None。按 Season 分组取每组最大 kickoff,再取
    全局最大的那一组——保证跨赛季边界时选到的是真正最新的一场,而不是
    某个固定字符串赛季下的最新一场。"""
    row = conn.execute(
        """
        SELECT Season AS season, MAX(kickoff_at_utc) AS kickoff_at_utc
        FROM dim_match
        WHERE League_ID = ? AND status = 'Finish' AND kickoff_at_utc IS NOT NULL
        GROUP BY Season
        ORDER BY MAX(kickoff_at_utc) DESC
        LIMIT 1
        """,
        (league_id,),
    ).fetchone()
    if row is None or not row["kickoff_at_utc"]:
        return None
    return {"season": row["season"], "kickoff_at_utc": row["kickoff_at_utc"]}


def _read_state(conn, league_id: int, season: str):
    row = conn.execute(
        "SELECT last_refreshed_at FROM standings_refresh_state"
        " WHERE league_id=? AND season=?",
        (league_id, season),
    ).fetchone()
    return row["last_refreshed_at"] if row else None


def _write_state(conn, league_id: int, season: str, kickoff_at_utc: str, now_iso: str) -> None:
    with tx(conn):
        conn.execute(
            """INSERT INTO standings_refresh_state
                 (league_id, season, last_refreshed_at, last_finished_kickoff_at_utc,
                  created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(league_id, season) DO UPDATE SET
                 last_refreshed_at=excluded.last_refreshed_at,
                 last_finished_kickoff_at_utc=excluded.last_finished_kickoff_at_utc,
                 updated_at=excluded.updated_at""",
            (league_id, season, now_iso, kickoff_at_utc, now_iso, now_iso),
        )


def _evaluate_league(conn, league_id: int, now_iso: str) -> dict:
    """阶段一:纯 DB 读,不发 HTTP、不写库——每轮上限只约束阶段二,这一步对
    全部 LEAGUE_META 联赛都便宜地跑一遍。返回 not_due/skipped 原样;到期
    候选返回 action="due"(还没真正刷新,留给阶段二按 due_at 挑选)。"""
    latest = _latest_finished_match(conn, league_id)
    if latest is None:
        return {"league_id": league_id, "action": "not_due", "reason": "no_finished_match"}

    season = latest["season"]
    if not season:
        return {
            "league_id": league_id, "action": "skipped",
            "reason": "latest_finished_match_missing_season",
        }

    last_refreshed_at = _read_state(conn, league_id, season)
    decision = due_refresh(
        latest_finished_kickoff_at_utc=latest["kickoff_at_utc"],
        last_refreshed_at=last_refreshed_at,
        now_iso=now_iso,
    )
    if not decision.due:
        return {
            "league_id": league_id, "season": season, "action": "not_due",
            "reason": decision.reason, "due_at": decision.due_at,
        }

    return {
        "league_id": league_id, "season": season, "action": "due",
        "due_at": decision.due_at, "kickoff_at_utc": latest["kickoff_at_utc"],
    }


def _refresh_league(conn, client, candidate: dict, now_iso: str) -> dict:
    """阶段二:真正执行 HTTP + DELETE/INSERT,并落库 state。只对阶段一挑出的
    到期候选调用——candidate 是 _evaluate_league 返回的 action="due" 那条。"""
    from ingest_league import ingest_season_tables

    league_id = candidate["league_id"]
    season = candidate["season"]
    kickoff_at_utc = candidate["kickoff_at_utc"]

    ingest_season_tables(client, league_id, season)
    _write_state(conn, league_id, season, kickoff_at_utc, now_iso)

    return {
        "league_id": league_id, "season": season, "action": "refreshed",
        "due_at": candidate["due_at"], "latest_finished_kickoff_at_utc": kickoff_at_utc,
    }


def run_due(now_iso: str | None = None) -> dict:
    now_iso = now_iso or utc_now_iso()
    conn = connect_rw("core")
    try:
        # 阶段一:LEAGUE_META 全部联赛做廉价的纯 DB 到期判断,单联赛异常不
        # 拖垮其它联赛。
        evaluated: dict[int, dict] = {}
        for league_id in sorted(LEAGUE_META):
            try:
                evaluated[league_id] = _evaluate_league(conn, league_id, now_iso)
            except Exception as exc:  # noqa: BLE001 — 单联赛失败不拖垮整轮
                evaluated[league_id] = {
                    "league_id": league_id, "action": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }

        # 按 due_at 最旧优先选出前 MAX_REFRESHES_PER_RUN 个真正刷新——不能按
        # league_id 排序,否则低 id 联赛永远优先、高 id 可能被持续饿死。
        due_candidates = [r for r in evaluated.values() if r["action"] == "due"]
        due_candidates.sort(key=lambda r: (r["due_at"], r["league_id"]))
        to_refresh = due_candidates[:MAX_REFRESHES_PER_RUN]
        to_defer = due_candidates[MAX_REFRESHES_PER_RUN:]

        # 到期但被上限挤掉的:不发 HTTP、不写 state,如实记 deferred——下一轮
        # 因为最旧优先必然被继续排到前面,不会被持续饿死,也不会静默丢失。
        for r in to_defer:
            evaluated[r["league_id"]] = {
                "league_id": r["league_id"], "season": r["season"],
                "action": "deferred", "reason": "per_run_cap", "due_at": r["due_at"],
            }

        # 阶段二:只对选中的候选真正发起 HTTP+DELETE/INSERT。FotMobClient
        # 只在这批里创建一次并复用,不是每联赛各 new 一个连接。
        if to_refresh:
            from backend.fotmob_client import FotMobClient

            client = FotMobClient()
            for candidate in to_refresh:
                league_id = candidate["league_id"]
                try:
                    evaluated[league_id] = _refresh_league(conn, client, candidate, now_iso)
                except Exception as exc:  # noqa: BLE001 — 单联赛失败不拖垮其它联赛
                    evaluated[league_id] = {
                        "league_id": league_id, "action": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }

        results = [evaluated[lid] for lid in sorted(evaluated)]
        by_action: dict[str, int] = {}
        for r in results:
            by_action[r["action"]] = by_action.get(r["action"], 0) + 1
        deferred_league_ids = sorted(
            r["league_id"] for r in results if r["action"] == "deferred"
        )

        return {
            "now": now_iso,
            "cap": MAX_REFRESHES_PER_RUN,
            "leagues": len(results),
            "refreshed": by_action.get("refreshed", 0),
            "deferred": by_action.get("deferred", 0),
            "errors": by_action.get("error", 0),
            "by_action": by_action,
            "deferred_league_ids": deferred_league_ids,
            "results": results,
        }
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--due", action="store_true", help="按到期判断执行(worker 默认调用方式)")
    ap.add_argument("--now", default=None, help="覆盖当前时间(ISO UTC,测试用)")
    args = ap.parse_args(argv)

    if not args.due:
        ap.error("需要 --due")
        return 2

    result = run_due(now_iso=args.now)
    print(json.dumps(result, ensure_ascii=False, default=str))
    if any(r["action"] == "error" for r in result["results"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
