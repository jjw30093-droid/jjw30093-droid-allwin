"""allwin.db(core)只读查询:比赛列表/详情/近况/排名/赛程 + 中文名 JOIN。"""

import sqlite3


def team_i18n_map(conn: sqlite3.Connection) -> dict[int, str]:
    try:
        return {
            r["Team_ID"]: r["name_zh"]
            for r in conn.execute("SELECT Team_ID, name_zh FROM dim_team_i18n")
        }
    except sqlite3.OperationalError:
        return {}


def _team_ref(team_id, name_en, zh_map) -> dict:
    return {
        "team_id": team_id,
        "name": zh_map.get(team_id) or name_en or "未知球队",
        "name_en": name_en,
    }


def _row_to_summary(r, zh_map) -> dict:
    return {
        "match_id": r["Match_ID"],
        "league_id": r["League_ID"],
        "season": r["Season"],
        "date_utc": r["Date"],
        "round": str(r["Match_Round"]) if r["Match_Round"] is not None else None,
        "status": r["status"],
        "home": _team_ref(r["Home_Team_ID"], r["Home_Team_Name"], zh_map),
        "away": _team_ref(r["Away_Team_ID"], r["Away_Team_Name"], zh_map),
        "home_score": r["home_score"],
        "away_score": r["away_score"],
    }


def list_matches(
    conn: sqlite3.Connection,
    league_ids: set[int],
    date: str | None = None,
    status: str | None = None,       # upcoming / finished / None
    league_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if league_id is not None:
        league_ids = league_ids & {league_id}
    if not league_ids:
        return {"total": 0, "matches": []}
    placeholders = ",".join("?" for _ in league_ids)
    where = [f"League_ID IN ({placeholders})"]
    params: list = list(league_ids)
    if date:
        where.append("Date = ?")
        params.append(date)
    if status == "upcoming":
        where.append("status IN ('NotStarted', 'InPlay')")
    elif status == "finished":
        where.append("status = 'Finish'")
    cond = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM dim_match WHERE {cond}", params).fetchone()[0]
    order = "Date DESC" if status == "finished" else "Date ASC"
    rows = conn.execute(
        f"SELECT * FROM dim_match WHERE {cond} ORDER BY {order}, Match_ID LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    zh = team_i18n_map(conn)
    return {"total": total, "matches": [_row_to_summary(r, zh) for r in rows]}


def match_by_id(conn: sqlite3.Connection, match_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM dim_match WHERE Match_ID=?", (match_id,)).fetchone()
    if r is None:
        return None
    return _row_to_summary(r, team_i18n_map(conn))


def recent_form(
    conn: sqlite3.Connection, team_id: int, before_date: str, limit: int = 5
) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM dim_match
           WHERE status='Finish' AND Date < ? AND (Home_Team_ID=? OR Away_Team_ID=?)
           ORDER BY Date DESC LIMIT ?""",
        (before_date, team_id, team_id, limit),
    ).fetchall()
    zh = team_i18n_map(conn)
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
                "opponent": _team_ref(opp_id, opp_name, zh),
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
    zh = team_i18n_map(conn)
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
            dict(r) | {"team": _team_ref(r["Team_ID"], r["Team_Name"], zh)} for r in rows
        ],
    }
