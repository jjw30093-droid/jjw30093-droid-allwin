"""odds.db 只读查询与 payload 形状归一(供 API 路由、bundle、silver 派生共用)。

背景(2026-08-06 审计 B2):bronze_ng_odds_snap.payload_json 存在两种真实形状——
- 嵌套 {"initial": {...}, "latest": {...}}:线上实时轮询链路
  (backend/ingest/odds_snapshots.py)写入,库中 65 行;
- 扁平 {"home":..,"draw":..,"away":..} / {"home":..,"line":..,"away":..} /
  {"over":..,"line":..,"under":..}:历史回填 CLI
  (backend/cli/ingest_nowgoal_historical_odds.py)写入,库中 734,812 行。

payload_json 在 SQL(裸 TEXT)/Pydantic(payload: dict)/生成 TS(宽 dict)三层
都没有形状契约,两个写入方各带一个只认自己形状的 silver 构建器,前端只认嵌套——
于是 2,152 场"完整走势"的赔率表对 Premium 渲染成整片 "—",且无一处报错。

修复方向是**读侧归一化**(写侧迁移会打破钉死嵌套形状的既有测试;
/odds 端点必须保持透传语义,同样有测试钉死)。本模块是归一化的唯一实现。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.models.research.market_baseline import (
    MIN_ODDS,
    OVERROUND_BOUNDS,
    proportional_devig,
)

# Bet365 在 bronze_ng_odds_snap 里有两个 company_id:'8' 是 2026-08-04 起的
# 实时轮询(唯一覆盖未来比赛的),'281' 是 2026-04-06 就停更的历史回填
# (对未来比赛贡献 0 场,但作为兜底不删)。同场两者都有时优先取 '8'。
_WIN_PROB_COMPANY_PRIORITY = ("8", "281")

# /odds 端点对无 odds:history_full 权益的请求方施加的同一条 1 小时延迟——
# 列表层的赔率概率字段必须遵守同一条纪律,否则会从列表页漏掉一个付费层的
# 新鲜度保证(见 routes_public.py 的 /matches/{id}/odds 实现)。
_DELAYED_LAG = timedelta(hours=1)


def normalize_odds_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """把两种真实 payload 形状归一为扁平字段组。

    嵌套形状取 latest,latest 缺失(None/空)时退回 initial;
    本就扁平的原样返回。输出恒为
    {home,draw,away} / {home,line,away} / {over,line,under} 之一。
    """
    if not isinstance(payload, dict):
        return {}
    if "latest" in payload or "initial" in payload:
        latest = payload.get("latest")
        if isinstance(latest, dict) and latest:
            return latest
        initial = payload.get("initial")
        if isinstance(initial, dict) and initial:
            return initial
        return {}
    return payload


def legacy_summary_points(
    conn_odds: sqlite3.Connection, fotmob_match_id: int, full: bool
) -> list[dict[str, Any]]:
    """旧项目两点摘要(bronze_legacy_odds_summary)的读取 + 权益投影。

    数据已在入库时归一为 canonical 方向(方向修正见
    backend/cli/ingest_legacy_odds.py),无逐条观测时间戳(§6.2:不伪装)。
    权益投影是服务端谓词而非下发后遮挡:
    - full(odds:history_full)→ initial+latest 两点(可看开临变化);
    - 否则只给 latest,initial 从不下发。

    routes_public 的 /odds 端点与 studio/bundle 共用本函数,保证两处
    对同一场比赛看到完全相同的点集。表不存在(旧测试库)时返回空列表。
    """
    if full:
        sql = (
            "SELECT market, period, source, provider, line,"
            " home_or_over, draw, away_or_under"
            " FROM bronze_legacy_odds_summary WHERE fotmob_match_id=?"
            " ORDER BY market, source, period"
        )
    else:
        sql = (
            "SELECT market, period, source, provider, line,"
            " home_or_over, draw, away_or_under"
            " FROM bronze_legacy_odds_summary"
            " WHERE fotmob_match_id=? AND period='latest'"
            " ORDER BY market, source"
        )
    try:
        rows = conn_odds.execute(sql, (fotmob_match_id,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def odds_coverage_sets(conn_odds: sqlite3.Connection) -> tuple[set[int], set[int]]:
    """(完整时间线比赛集合, 两点摘要比赛集合)——每请求算一次,不逐行查。

    列表层用它给每场比赛标 odds_coverage_tier:
    full_timeline 优先于 open_close_only(同场两者皆有时按更高档展示)。
    """
    try:
        full = {
            int(row[0])
            for row in conn_odds.execute(
                """SELECT DISTINCT x.fotmob_match_id
                     FROM dim_match_xref x
                     JOIN bronze_ng_odds_snap b
                       ON b.provider_match_id=x.provider_match_id
                    WHERE x.provider='nowgoal'
                      AND x.review_status IN ('auto_ok','confirmed')"""
            )
        }
    except sqlite3.OperationalError:
        full = set()
    try:
        legacy = {
            int(row[0])
            for row in conn_odds.execute(
                "SELECT DISTINCT fotmob_match_id FROM bronze_legacy_odds_summary"
            )
        }
    except sqlite3.OperationalError:
        legacy = set()
    return full, legacy


def latest_1x2_by_match(
    conn_odds: sqlite3.Connection, *, now_iso: str, delayed: bool
) -> dict[int, dict[str, Any]]:
    """所有比赛的最新 Bet365 1x2 去水概率——一次查询,不逐场请求。

    首页/列表页的胜平负概率条共用本函数(见 CLAUDE.md 首页改版计划):字段
    只在这一处产出,routes_public.py 的 list_matches 与 /matches/{id} 都从
    这里取,不允许出现第二套各自实现的去水逻辑。

    口径(与 backend/models/research/market_baseline.py 的研究基线同一套
    去水公式与拒绝规则,但覆盖范围不同——那边只服务 5 大联赛已完赛比赛做
    回测,这里服务全部联赛的当前比赛,且必须包含 company_id '8' 实时轮询):
    - market='1x2' AND market_phase='pre_match';
    - xref 只认 review_status ∈ (auto_ok, confirmed),无法安全映射的 fail closed;
    - 每场每公司取 observed_at 最新一条(并列取 id 最大);
    - 公司优先级 8 > 281(见模块顶部注释);
    - 比例去水 p_i = (1/odds_i) / Σ_j(1/odds_j);
    - 任一赔率缺失/≤1.01,或 overround 不在 [1.01, 1.35] → 该场剔除,不补 0、不猜;
    - delayed=True(无 odds:history_full 权益)时只认 observed_at 早于
      now-1h 的快照,与 /matches/{id}/odds 端点的匿名延迟策略一致。

    返回的每场只有一个 observed_at——三段概率条底部必须显示这个时间,
    不得把它当成"实时"文案(数据可能是几小时甚至更久前的快照,§6.2 不伪装)。
    """
    placeholders = ",".join("?" for _ in _WIN_PROB_COMPANY_PRIORITY)
    try:
        rows = conn_odds.execute(
            f"""SELECT b.id, x.fotmob_match_id, b.company_id, b.payload_json, b.observed_at
                  FROM bronze_ng_odds_snap b
                  JOIN dim_match_xref x
                    ON x.provider_match_id = b.provider_match_id AND x.provider='nowgoal'
                 WHERE b.market='1x2' AND b.market_phase='pre_match'
                   AND b.company_id IN ({placeholders})
                   AND x.review_status IN ('auto_ok','confirmed')""",
            _WIN_PROB_COMPANY_PRIORITY,
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    cutoff = None
    if delayed:
        cutoff = (
            datetime.fromisoformat(now_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
            - _DELAYED_LAG
        )

    best: dict[tuple[int, str], tuple[str, int, dict]] = {}
    for r in rows:
        obs = str(r["observed_at"])
        if cutoff is not None:
            obs_dt = datetime.fromisoformat(obs.replace("Z", "+00:00")).astimezone(timezone.utc)
            if obs_dt > cutoff:
                continue
        fid = int(r["fotmob_match_id"])
        cid = str(r["company_id"])
        key = (fid, cid)
        cur = best.get(key)
        cand = (obs, int(r["id"]))
        if cur is None or cand > (cur[0], cur[1]):
            try:
                payload = json.loads(r["payload_json"])
            except (TypeError, ValueError):
                continue
            best[key] = (obs, int(r["id"]), payload)

    fids = {fid for fid, _cid in best}
    out: dict[int, dict[str, Any]] = {}
    for fid in fids:
        chosen = None
        for cid in _WIN_PROB_COMPANY_PRIORITY:
            chosen = best.get((fid, cid))
            if chosen is not None:
                break
        if chosen is None:
            continue
        obs, _id, payload = chosen
        flat = normalize_odds_payload(payload)
        h, d, a = flat.get("home"), flat.get("draw"), flat.get("away")
        if not all(isinstance(v, (int, float)) and v > MIN_ODDS for v in (h, d, a)):
            continue
        p_home, p_draw, p_away, overround = proportional_devig(h, d, a)
        if not (OVERROUND_BOUNDS[0] <= overround <= OVERROUND_BOUNDS[1]):
            continue
        out[fid] = {
            "p_home": round(p_home, 4),
            "p_draw": round(p_draw, 4),
            "p_away": round(p_away, 4),
            "observed_at": obs,
        }
    return out
