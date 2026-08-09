"""allwin.db(core)只读查询:比赛列表/详情/近况/排名/赛程 + 中文名 JOIN。"""

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from backend.media.team_crests import resolve_team_crest_url
from backend.queries.teams import (
    display_name_for_team,
    provider_name_for_team,
    team_display_map,
)


def team_i18n_map(conn: sqlite3.Connection) -> dict[int, str]:
    return {
        team_id: row["name_zh"]
        for team_id, row in team_display_map(conn).items()
        if row.get("name_zh")
    }


def _team_ref(team_id, name_en, display) -> dict:
    return {
        "team_id": team_id,
        "name": display_name_for_team(
            team_id, provider_name=name_en, display=display
        ),
        "name_en": provider_name_for_team(
            team_id, provider_name=name_en, display=display
        ),
        "crest_url": resolve_team_crest_url("fotmob", team_id),
    }


def _row_to_summary(r, display) -> dict:
    return {
        "match_id": r["Match_ID"],
        "league_id": r["League_ID"],
        "season": r["Season"],
        "date_utc": r["Date"],
        # 精确开球时刻可空(§6.2.1);旧测试布景可能没有该列,容错取 None
        "kickoff_at_utc": r["kickoff_at_utc"] if "kickoff_at_utc" in r.keys() else None,
        "round": str(r["Match_Round"]) if r["Match_Round"] is not None else None,
        "status": r["status"],
        "home": _team_ref(r["Home_Team_ID"], r["Home_Team_Name"], display),
        "away": _team_ref(r["Away_Team_ID"], r["Away_Team_Name"], display),
        "home_score": r["home_score"],
        "away_score": r["away_score"],
}


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _window_bounds(
    window: str | None, now: datetime
) -> tuple[str | None, str | None]:
    if window in (None, "all"):
        return None, None
    current = now.astimezone(timezone.utc)
    if window in {"today", "tomorrow"}:
        local = current.astimezone(ZoneInfo("Asia/Shanghai"))
        day_offset = 1 if window == "tomorrow" else 0
        start_local = (local + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return _iso_utc(start_local), _iso_utc(start_local + timedelta(days=1))
    days = {"3d": 3, "7d": 7}.get(window)
    if days is None:
        raise ValueError("unsupported match window")
    return _iso_utc(current), _iso_utc(current + timedelta(days=days))


def list_matches(
    conn: sqlite3.Connection,
    league_ids: set[int],
    date: str | None = None,
    status: str | None = None,       # upcoming / finished / None
    league_id: int | None = None,
    season: str | None = None,
    window: str | None = None,
    query: str | None = None,
    query_team_ids: set[int] | None = None,
    match_ids: set[int] | None = None,
    priority_match_ids: set[int] | None = None,
    now: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if league_id is not None:
        league_ids = league_ids & {league_id}
    if not league_ids:
        return {"total": 0, "matches": []}
    ordered_league_ids = sorted(league_ids)
    placeholders = ",".join("?" for _ in ordered_league_ids)
    where = [f"League_ID IN ({placeholders})"]
    params: list = ordered_league_ids
    if date:
        where.append("Date = ?")
        params.append(date)
    if season is not None:
        # 赛季过滤必须发生在 SQL LIMIT 之前——曾在端点层做 Python 后筛,
        # 多赛季联赛(如五大联赛 6 季)下 LIMIT 先截走别的赛季,目标赛季 0 命中
        where.append("Season = ?")
        params.append(season)
    if status == "upcoming":
        where.append("status IN ('NotStarted', 'InPlay')")
    elif status == "finished":
        where.append("status = 'Finish'")
    start, end = _window_bounds(window, now or datetime.now(timezone.utc))
    if start is not None:
        where.append("julianday(kickoff_at_utc) >= julianday(?)")
        params.append(start)
    if end is not None:
        where.append("julianday(kickoff_at_utc) < julianday(?)")
        params.append(end)
    cleaned_query = query.strip() if isinstance(query, str) else ""
    if cleaned_query:
        pattern = f"%{cleaned_query}%"
        try:
            i18n_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(dim_team_i18n)").fetchall()
            }
        except sqlite3.OperationalError:
            i18n_columns = set()
        i18n_name_en = (
            "t.name_en LIKE ? COLLATE NOCASE OR " if "name_en" in i18n_columns else ""
        )
        alias_sql = ""
        alias_params: list[int] = []
        if query_team_ids:
            ordered_team_ids = sorted(query_team_ids)
            alias_placeholders = ",".join("?" for _ in ordered_team_ids)
            alias_sql = (
                f" OR Home_Team_ID IN ({alias_placeholders})"
                f" OR Away_Team_ID IN ({alias_placeholders})"
            )
            alias_params = ordered_team_ids * 2
        where.append(
            f"""(
              Home_Team_Name LIKE ? COLLATE NOCASE OR
              Away_Team_Name LIKE ? COLLATE NOCASE OR
              EXISTS (
                SELECT 1 FROM dim_team_i18n t
                 WHERE t.Team_ID IN (dim_match.Home_Team_ID, dim_match.Away_Team_ID)
                   AND (t.name_zh LIKE ? COLLATE NOCASE OR {i18n_name_en} 0)
              )
              {alias_sql}
            )"""
        )
        params.extend(
            [pattern, pattern, pattern]
            + ([pattern] if i18n_name_en else [])
            + alias_params
        )
    if match_ids is not None:
        if not match_ids:
            return {"total": 0, "matches": []}
        ordered_match_ids = sorted(match_ids)
        id_placeholders = ",".join("?" for _ in ordered_match_ids)
        where.append(f"Match_ID IN ({id_placeholders})")
        params.extend(ordered_match_ids)
    cond = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM dim_match WHERE {cond}", params).fetchone()[0]
    order_params: list[int] = []
    # 统一时间轴:COALESCE(kickoff_at_utc, Date) 而不是把"没有精确开球时间"的
    # 行整体压到最后再按 Match_ID 排——那样会让同一批比赛(如整赛季只有自然日
    # 粒度的英超 2026/2027)在多联赛混排列表里完全失去时间顺序,被排在任何有
    # 精确 kickoff 的联赛(哪怕是几个月后的比赛)后面(CLAUDE.md §6.2.1:不伪装
    # 精确时间,但也不能因为"不精确"就放弃参与时间排序——Date 本身是真实来源
    # 数据,只是粒度粗;北京时间是固定 UTC+8 无夏令时偏移,按 UTC 排序等价于
    # 按北京时间排序,不需要额外转换)。
    if status == "finished":
        order = "julianday(COALESCE(kickoff_at_utc, Date)) DESC, Match_ID DESC"
    else:
        priority_order = ""
        if priority_match_ids:
            ordered_priority_ids = sorted(priority_match_ids)
            priority_placeholders = ",".join("?" for _ in ordered_priority_ids)
            priority_order = (
                f"CASE WHEN Match_ID IN ({priority_placeholders}) THEN 0 ELSE 1 END, "
            )
            order_params = ordered_priority_ids
        order = (
            priority_order
            + "julianday(COALESCE(kickoff_at_utc, Date)) ASC, Match_ID ASC"
        )
    rows = conn.execute(
        f"SELECT * FROM dim_match WHERE {cond} ORDER BY {order} LIMIT ? OFFSET ?",
        params + order_params + [limit, offset],
    ).fetchall()
    display = team_display_map(conn)
    return {"total": total, "matches": [_row_to_summary(r, display) for r in rows]}


def match_by_id(conn: sqlite3.Connection, match_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM dim_match WHERE Match_ID=?", (match_id,)).fetchone()
    if r is None:
        return None
    return _row_to_summary(r, team_display_map(conn))


def recent_form(
    conn: sqlite3.Connection, team_id: int, before_date: str, limit: int = 5
) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM dim_match
           WHERE status='Finish' AND Date < ? AND (Home_Team_ID=? OR Away_Team_ID=?)
           ORDER BY Date DESC LIMIT ?""",
        (before_date, team_id, team_id, limit),
    ).fetchall()
    display = team_display_map(conn)
    out = []
    for r in rows:
        is_home = r["Home_Team_ID"] == team_id
        gf = r["home_score"] if is_home else r["away_score"]
        ga = r["away_score"] if is_home else r["home_score"]
        if gf is None or ga is None:
            continue
        opp_id = r["Away_Team_ID"] if is_home else r["Home_Team_ID"]
        opp_name = r["Away_Team_Name"] if is_home else r["Home_Team_Name"]
        out.append(
            {
                "match_id": r["Match_ID"],
                "date_utc": r["Date"],
                "opponent": _team_ref(opp_id, opp_name, display),
                "venue": "home" if is_home else "away",
                "goals_for": gf,
                "goals_against": ga,
                "result": "W" if gf > ga else ("L" if gf < ga else "D"),
            }
        )
    return out


def seasons_of_league(conn: sqlite3.Connection, league_id: int) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT Season FROM dim_match WHERE League_ID=? ORDER BY Season", (league_id,)
        )
    ]


def default_fixture_season(conn: sqlite3.Connection, league_id: int, seasons: list[str]) -> str | None:
    """赛程默认赛季 = 最早一场未开赛比赛所在的赛季;没有未开赛比赛则回退最新赛季。

    不用 max(Season):英超 2026/2027 已有赛程但 2025/2026 才是"最新已完赛"——
    max(Season) 两种语境下答案不同,这里明确回答"用户现在最该看哪个赛季"。
    不用"最新一个有 NotStarted 的赛季":未来若同时存在两个都有未开赛比赛的
    赛季(如跨赛季衔接期),那个定义会挑出开赛更晚的一个,这里始终挑今晚的球。
    """
    row = conn.execute(
        """SELECT Season FROM dim_match
             WHERE League_ID=? AND status IN ('NotStarted','InPlay')
             ORDER BY COALESCE(kickoff_at_utc, Date) ASC, Match_ID ASC
             LIMIT 1""",
        (league_id,),
    ).fetchone()
    if row is not None:
        return row[0]
    return seasons[-1] if seasons else None


def standings(conn: sqlite3.Connection, league_id: int, season: str | None = None) -> dict:
    try:
        seasons = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT Season FROM fact_league_table WHERE League_ID=? ORDER BY Season",
                (league_id,),
            )
        ]
    except sqlite3.OperationalError:
        seasons = []
    if not seasons:
        return {"season": season, "available_seasons": [], "rows": []}
    if season is None or season not in seasons:
        season = seasons[-1]
    display = team_display_map(conn)
    rows = conn.execute(
        """SELECT Team_ID, Team_Name, position, played, wins, draws, losses,
                  goals_for, goals_against, goal_diff, points, qual_color
           FROM fact_league_table
           WHERE League_ID=? AND Season=? AND table_type='all' ORDER BY position""",
        (league_id, season),
    ).fetchall()
    return {
        "season": season,
        "available_seasons": seasons,
        "rows": [
            dict(r) | {"team": _team_ref(r["Team_ID"], r["Team_Name"], display)}
            for r in rows
        ],
    }
