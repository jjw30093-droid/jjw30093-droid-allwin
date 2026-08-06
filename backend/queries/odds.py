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

import sqlite3
from typing import Any


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
