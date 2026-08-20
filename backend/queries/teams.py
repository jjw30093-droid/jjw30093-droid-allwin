"""Single public team-display projection shared by match and league queries."""

from __future__ import annotations

import re
import sqlite3
from typing import Any


PENDING_TEAM_NAME = "球队名称待同步"
_INTERNAL_PLACEHOLDER = re.compile(r"^team\s+\d+$", re.IGNORECASE)


def _usable_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or name.isdigit() or _INTERNAL_PLACEHOLDER.fullmatch(name):
        return None
    return name


def team_display_map(conn: sqlite3.Connection) -> dict[int, dict[str, str | None]]:
    display: dict[int, dict[str, str | None]] = {}

    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(dim_team_i18n)").fetchall()
        }
        name_en_expr = "name_en" if "name_en" in columns else "NULL AS name_en"
        for row in conn.execute(
            f"SELECT Team_ID, name_zh, {name_en_expr} FROM dim_team_i18n"
        ):
            team_id = int(row["Team_ID"])
            display[team_id] = {
                "name_zh": _usable_name(row["name_zh"]),
                "provider_name": _usable_name(row["name_en"]),
            }
    except sqlite3.OperationalError:
        pass

    provider_queries = (
        """SELECT Home_Team_ID AS Team_ID, Home_Team_Name AS Team_Name
             FROM dim_match
           UNION ALL
           SELECT Away_Team_ID, Away_Team_Name FROM dim_match""",
        "SELECT Team_ID, Team_Name FROM fact_league_table",
    )
    for sql in provider_queries:
        try:
            rows = conn.execute(sql).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            if row["Team_ID"] is None:
                continue
            team_id = int(row["Team_ID"])
            provider_name = _usable_name(row["Team_Name"])
            if provider_name is None:
                continue
            current = display.setdefault(
                team_id, {"name_zh": None, "provider_name": None}
            )
            if current["provider_name"] is None:
                current["provider_name"] = provider_name
    return display


def team_display_for(
    conn: sqlite3.Connection, team_ids: set[int]
) -> dict[int, dict[str, str | None]]:
    """team_display_map 的按需(scoped)版本(2026-08-19 性能修复)。

    team_display_map() 为了给出 dim_team_i18n 里 304 支球队的译名,每次都
    UNION ALL 扫全部 dim_match(33,868 行)再扫 fact_league_table,逐行跑
    _usable_name 正则——单次 66–75ms,且被同一请求内的多个调用点各自独立
    调用(match_by_id/recent_form ×2/... 一次详情请求里跑 3 次)。这里只对
    调用方点名的 team_id 做同样的三层来源查找(dim_team_i18n → dim_match →
    fact_league_table,先到先得、不覆盖已有值),用 WHERE Team_ID IN (...)
    把结果收窄到几个 id——生产实测同样的数据从 33ms 降到 5ms 量级。

    与 team_display_map 的等价性由 tests/backend/test_team_display_for.py
    逐条钉住,不得因为"性能优化"而悄悄丢掉任一层来源或改变优先级。
    """
    # dim_match.Home_Team_ID/Away_Team_ID 理论上可空(schema 未加 NOT NULL);
    # 调用方常常是 {r["Home_Team_ID"], r["Away_Team_ID"], ...} 这类直接从行
    # 取出的集合,可能混进 None——原版 team_display_map 全扫时用
    # `if row["Team_ID"] is None: continue` 跳过,这里在入口统一过滤,
    # 否则 sorted() 在 int 与 None 混排时会直接抛 TypeError。
    ids = sorted(tid for tid in team_ids if tid is not None)
    if not ids:
        return {}
    display: dict[int, dict[str, str | None]] = {}
    placeholders = ",".join("?" for _ in ids)

    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(dim_team_i18n)").fetchall()
        }
        name_en_expr = "name_en" if "name_en" in columns else "NULL AS name_en"
        for row in conn.execute(
            f"SELECT Team_ID, name_zh, {name_en_expr} FROM dim_team_i18n"
            f" WHERE Team_ID IN ({placeholders})",
            ids,
        ):
            team_id = int(row["Team_ID"])
            display[team_id] = {
                "name_zh": _usable_name(row["name_zh"]),
                "provider_name": _usable_name(row["name_en"]),
            }
    except sqlite3.OperationalError:
        pass

    provider_queries = (
        (
            f"""SELECT Home_Team_ID AS Team_ID, Home_Team_Name AS Team_Name
                  FROM dim_match WHERE Home_Team_ID IN ({placeholders})
                UNION ALL
                SELECT Away_Team_ID, Away_Team_Name FROM dim_match
                 WHERE Away_Team_ID IN ({placeholders})""",
            ids + ids,
        ),
        (
            f"SELECT Team_ID, Team_Name FROM fact_league_table WHERE Team_ID IN ({placeholders})",
            ids,
        ),
    )
    for sql, params in provider_queries:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            if row["Team_ID"] is None:
                continue
            team_id = int(row["Team_ID"])
            provider_name = _usable_name(row["Team_Name"])
            if provider_name is None:
                continue
            current = display.setdefault(
                team_id, {"name_zh": None, "provider_name": None}
            )
            if current["provider_name"] is None:
                current["provider_name"] = provider_name
    return display


def display_name_for_team(
    team_id: int | None,
    *,
    provider_name: str | None = None,
    display: dict[int, dict[str, str | None]] | None = None,
) -> str:
    current = (display or {}).get(team_id or -1, {})
    return (
        _usable_name(current.get("name_zh"))
        or _usable_name(provider_name)
        or _usable_name(current.get("provider_name"))
        or PENDING_TEAM_NAME
    )


def provider_name_for_team(
    team_id: int | None,
    *,
    provider_name: str | None = None,
    display: dict[int, dict[str, str | None]] | None = None,
) -> str | None:
    current = (display or {}).get(team_id or -1, {})
    return _usable_name(provider_name) or _usable_name(current.get("provider_name"))
