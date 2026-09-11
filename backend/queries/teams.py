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


_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def team_brand_color_map(
    conn: sqlite3.Connection, league_id: int, season: str | None
) -> dict[int, dict[str, str]]:
    """该联赛该赛季每支球队的一组代表色(浅色/深色各一个十六进制值)。

    数据源是 dim_match 的 {Home,Away}_Team_Color_{Light,Dark}(migration 0008
    起写入)。**这不是"官方固定队色"**:FotMob 给的是按对手做过撞色规避的
    **配对级**结果,同一支队换个对手这两个值可能不同(见 schemas.TeamColorPair
    的说明)。榜单胶囊只需要一个"看得出是哪支队"的代表色,所以这里取该队在本
    赛季**最近一场**有颜色的比赛的取值,保证同一请求内确定、不随机跳动。

    只认深浅两个变体都齐的球队。刻意不拿 fact_season_*_stats.extra_json 的
    TeamColor 兜底——那个字段只有浅色变体(实测 = FotMob 的 lightMode 值),
    在深色卡片上用浅色变体正是 CLAUDE.md §11.3 记过的"白压白隐形"同一类
    错误;宁可这支队没有颜色、前端回退品牌色,也不跨主题借用。

    2026-09-11 生产实测:当前赛季 13/14 个在营联赛球队覆盖率 100%(欧冠 36/36、
    英超 20/20 等),只有澳超(113)当前赛季为 0(其比赛早于队色列上线),该联赛
    与历史赛季会整体回退品牌色。
    """
    if season is None:
        return {}
    colors: dict[int, dict[str, str]] = {}
    try:
        rows = conn.execute(
            """SELECT Team_ID, light, dark FROM (
                   SELECT Home_Team_ID AS Team_ID,
                          Home_Team_Color_Light AS light,
                          Home_Team_Color_Dark AS dark,
                          COALESCE(kickoff_at_utc, Date) AS ts
                     FROM dim_match
                    WHERE League_ID=? AND Season=?
                      AND Home_Team_Color_Light IS NOT NULL
                      AND Home_Team_Color_Dark IS NOT NULL
                   UNION ALL
                   SELECT Away_Team_ID,
                          Away_Team_Color_Light,
                          Away_Team_Color_Dark,
                          COALESCE(kickoff_at_utc, Date)
                     FROM dim_match
                    WHERE League_ID=? AND Season=?
                      AND Away_Team_Color_Light IS NOT NULL
                      AND Away_Team_Color_Dark IS NOT NULL
               ) ORDER BY ts ASC""",
            (league_id, season, league_id, season),
        ).fetchall()
    except sqlite3.OperationalError:
        # 老库还没跑 0008(队色列不存在)——整体回退品牌色,不让榜单页 500
        return {}
    for row in rows:
        team_id = row["Team_ID"]
        light, dark = row["light"], row["dark"]
        if team_id is None or not isinstance(light, str) or not isinstance(dark, str):
            continue
        if not _HEX_RE.fullmatch(light.strip()) or not _HEX_RE.fullmatch(dark.strip()):
            continue
        # ORDER BY ts ASC + 直接覆盖 = 最终留下最近一场的取值
        colors[int(team_id)] = {"light": light.strip(), "dark": dark.strip()}
    return colors


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
