"""
api_server.py — FastAPI serving 层(ROADMAP.md Phase 1.3)。

前端只读 API,不直连 DB(CLAUDE.md §2)。本文件是前端与数据之间的唯一通道。

免费 / 付费边界按字段级分级(CLAUDE.md §3),gate 落在 endpoint 层:
    - /api/league/{league_id}/overview  — 免费,不调用 require_membership。
    - /api/league/{league_id}/betting   — 付费,调用 require_membership 占位。
    - /api/league/{league_id}/matches   — 免费(赛程/结果)。
silver_team_season_stats 是混合表:overview 只 SELECT 免费字段
(射门/射正/控球/xG/xGOT),betting 才 SELECT 角球/红黄牌/BTTS/零封字段,
同表按字段拆,不整表返(CLAUDE.md §3)。

中文名在本层 JOIN dim_team_i18n / dim_player_i18n 返回,前端不碰映射
(CLAUDE.md §2)。

只读 DB:用 sqlite3 的 mode=ro URI 连接,从连接层面禁止写入,不只是
"代码里不写"(CLAUDE.md §9/§10 的验证精神——用结构保证,而非自觉)。
"""

import os
import sqlite3
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from db import DB_PATH

app = FastAPI(title="allwin serving API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_readonly_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def require_membership(request: Request) -> bool:
    # TODO: 1.6 接入真实 entitlement 校验(FastAPI 侧读 subscription 状态)。
    # 现在直接放行,只是结构上确保每个付费 endpoint 都过这一道口子,
    # 后续接真实校验时只改这一个函数,不用逐个 endpoint 改。
    return True


def _rows_to_dicts(rows) -> list:
    return [dict(r) for r in rows]


def _valid_seasons(conn: sqlite3.Connection, league_id: int) -> list:
    rows = conn.execute(
        "SELECT DISTINCT Season FROM dim_match WHERE League_ID = ? ORDER BY Season",
        (league_id,),
    ).fetchall()
    return [r["Season"] for r in rows]


def _resolve_season(conn: sqlite3.Connection, league_id: int, season: Optional[str]) -> str:
    seasons = _valid_seasons(conn, league_id)
    if not seasons:
        raise HTTPException(status_code=400, detail=f"league_id={league_id} 无数据")
    if season is None:
        return seasons[-1]
    if season not in seasons:
        raise HTTPException(
            status_code=400,
            detail=f"非法 season={season!r},league_id={league_id} 可用赛季: {seasons}",
        )
    return season


def _team_i18n_map(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT Team_ID, name_zh FROM dim_team_i18n").fetchall()
    return {r["Team_ID"]: r["name_zh"] for r in rows}


def _player_i18n_map(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT Player_ID, name_zh, name_zh_short FROM dim_player_i18n"
    ).fetchall()
    return {r["Player_ID"]: {"name_zh": r["name_zh"], "name_zh_short": r["name_zh_short"]} for r in rows}


# ── 免费:GET /api/league/{league_id}/overview ────────────────────────
FREE_PLAYER_STATS = {
    "goals": "进球",
    "goal_assist": "助攻",
    "expected_goals": "xG",
    "expected_goalsontarget": "xGOT",
    "rating": "评分",
}


@app.get("/api/league/{league_id}/overview")
def league_overview(league_id: int, season: Optional[str] = None):
    conn = get_readonly_connection()
    try:
        season = _resolve_season(conn, league_id, season)
        team_zh = _team_i18n_map(conn)
        player_zh = _player_i18n_map(conn)

        standings_rows = conn.execute(
            """
            SELECT Team_ID, position, played, wins, draws, losses,
                   goals_for, goals_against, goal_diff, points, qual_color
            FROM fact_league_table
            WHERE League_ID = ? AND Season = ? AND table_type = 'all'
            ORDER BY position
            """,
            (league_id, season),
        ).fetchall()
        standings = []
        for r in standings_rows:
            d = dict(r)
            team_id = d.pop("Team_ID")
            d["team_id"] = team_id
            d["team_name_zh"] = team_zh.get(team_id)
            standings.append(d)

        league_summary_row = conn.execute(
            """
            SELECT total_matches, home_win_pct, draw_pct, away_win_pct, avg_total_goals
            FROM silver_league_season_summary
            WHERE League_ID = ? AND Season = ?
            """,
            (league_id, season),
        ).fetchone()
        league_summary = dict(league_summary_row) if league_summary_row else None

        team_stats_rows = conn.execute(
            """
            SELECT Team_ID, matches_played, avg_total_shots, avg_shots_on_target,
                   avg_possession, avg_expected_goals, avg_expected_goals_on_target
            FROM silver_team_season_stats
            WHERE League_ID = ? AND Season = ?
            """,
            (league_id, season),
        ).fetchall()
        team_stats = []
        for r in team_stats_rows:
            d = dict(r)
            team_id = d.pop("Team_ID")
            d["team_id"] = team_id
            d["team_name_zh"] = team_zh.get(team_id)
            team_stats.append(d)

        player_leaderboards = {}
        for stat_name, label_zh in FREE_PLAYER_STATS.items():
            rows = conn.execute(
                """
                SELECT Player_ID, Player_Name, Team_ID, Team_Name, rank, value
                FROM fact_season_player_stats
                WHERE League_ID = ? AND Season = ? AND stat_name = ?
                ORDER BY rank
                LIMIT 10
                """,
                (league_id, season, stat_name),
            ).fetchall()
            entries = []
            for r in rows:
                d = dict(r)
                pid = d.pop("Player_ID")
                d["player_id"] = pid
                i18n = player_zh.get(pid, {})
                d["player_name_zh"] = i18n.get("name_zh")
                d["player_name_zh_short"] = i18n.get("name_zh_short")
                d["team_name_zh"] = team_zh.get(d.get("Team_ID"))
                entries.append(d)
            player_leaderboards[stat_name] = {"label_zh": label_zh, "entries": entries}

        return {
            "league_id": league_id,
            "season": season,
            "standings": standings,
            "league_summary": league_summary,
            "team_stats": team_stats,
            "player_leaderboards": player_leaderboards,
        }
    finally:
        conn.close()


# ── 付费:GET /api/league/{league_id}/betting ─────────────────────────
@app.get("/api/league/{league_id}/betting")
def league_betting(request: Request, league_id: int, season: Optional[str] = None):
    require_membership(request)

    conn = get_readonly_connection()
    try:
        season = _resolve_season(conn, league_id, season)
        team_zh = _team_i18n_map(conn)

        over_under_rows = conn.execute(
            """
            SELECT threshold, over_count, under_count, over_pct, under_pct
            FROM silver_over_under_thresholds
            WHERE League_ID = ? AND Season = ?
            ORDER BY threshold
            """,
            (league_id, season),
        ).fetchall()

        score_distribution_rows = conn.execute(
            """
            SELECT home_score, away_score, match_count, pct
            FROM silver_score_distribution
            WHERE League_ID = ? AND Season = ?
            ORDER BY pct DESC
            """,
            (league_id, season),
        ).fetchall()

        goal_minute_bucket_rows = conn.execute(
            """
            SELECT bucket, goal_count, pct
            FROM silver_goal_minute_buckets
            WHERE League_ID = ? AND Season = ?
            """,
            (league_id, season),
        ).fetchall()

        team_betting_rows = conn.execute(
            """
            SELECT Team_ID, matches_played, avg_corners, avg_yellow_cards, avg_red_cards,
                   clean_sheets, btts_matches, btts_pct
            FROM silver_team_season_stats
            WHERE League_ID = ? AND Season = ?
            """,
            (league_id, season),
        ).fetchall()
        team_betting_stats = []
        for r in team_betting_rows:
            d = dict(r)
            team_id = d.pop("Team_ID")
            d["team_id"] = team_id
            d["team_name_zh"] = team_zh.get(team_id)
            team_betting_stats.append(d)

        return {
            "league_id": league_id,
            "season": season,
            "over_under": _rows_to_dicts(over_under_rows),
            "score_distribution": _rows_to_dicts(score_distribution_rows),
            "goal_minute_buckets": _rows_to_dicts(goal_minute_bucket_rows),
            "team_betting_stats": team_betting_stats,
        }
    finally:
        conn.close()


# ── 免费:GET /api/league/{league_id}/matches ─────────────────────────
@app.get("/api/league/{league_id}/matches")
def league_matches(league_id: int, season: Optional[str] = None):
    conn = get_readonly_connection()
    try:
        season = _resolve_season(conn, league_id, season)
        team_zh = _team_i18n_map(conn)

        rows = conn.execute(
            """
            SELECT Match_ID, Date, Home_Team_ID, Away_Team_ID,
                   home_score, away_score, status, Match_Round
            FROM dim_match
            WHERE League_ID = ? AND Season = ?
            ORDER BY Date
            """,
            (league_id, season),
        ).fetchall()

        matches = []
        for r in rows:
            d = dict(r)
            home_id, away_id = d.pop("Home_Team_ID"), d.pop("Away_Team_ID")
            d["home_team_id"] = home_id
            d["away_team_id"] = away_id
            d["home_team_name_zh"] = team_zh.get(home_id)
            d["away_team_name_zh"] = team_zh.get(away_id)
            matches.append(d)

        return {
            "league_id": league_id,
            "season": season,
            "matches": matches,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
