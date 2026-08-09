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
