"""
api_server.py — FastAPI serving 层(ROADMAP.md Phase 1.3)。

前端只读 API,不直连 DB(CLAUDE.md §2)。本文件是前端与数据之间的唯一通道。

2026-08-16 产品权限口径修正(经用户批准):除"每日精选"外,网站所有比赛
内容全部免费,包括匿名用户——本文件 legacy 端点均不再有任何
entitlement 门禁:
    - /api/league/{league_id}/overview
    - /api/league/{league_id}/betting         — 此前调用 require_membership,现已移除。
    - /api/league/{league_id}/matches
2026-08-25:WDL 模型与正式预测登记簿已整体废弃(胜率改由 bet365 赔率直接
派生),/api/league/{league_id}/wdl-predictions 端点随之删除。
silver_team_season_stats 是混合表:overview 只 SELECT 部分字段(射门/射正/
控球/xG/xGOT),betting 额外 SELECT 角球/红黄牌/BTTS/零封字段——这只是两个
端点各自的历史字段选择差异,不再是免费/付费投影边界。

中文名在本层 JOIN dim_team_i18n / dim_player_i18n 返回,前端不碰映射
(CLAUDE.md §2)。

只读 DB:用 sqlite3 的 mode=ro URI 连接,从连接层面禁止写入,不只是
"代码里不写"(CLAUDE.md §9/§10 的验证精神——用结构保证,而非自觉)。
"""

import os
import sqlite3
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

try:
    from backend.db import DB_PATH          # 包内运行(backend.api.app 装配)
except ImportError:
    from db import DB_PATH                  # 兼容旧脚本式运行(cwd=backend)

from backend.api.cache_policy import install_cache_policy
from backend.api.error_handlers import register_error_handlers
from backend.api.schemas import (
    LeagueBettingResponse,
    LeagueMatchesResponse,
    LeagueOverviewResponse,
    error_responses,
)
from backend.media.team_crests import resolve_team_crest_url
from backend.queries.teams import display_name_for_team, team_display_map

app = FastAPI(title="allwin serving API")

# 全站统一错误契约(CLAUDE.md §10):本文件的 OpenAPI 声明(response_model/
# error_responses)早已引用 ApiErrorDTO,但作为可独立运行的 FastAPI app(见文末
# `if __name__ == "__main__"`),必须自己也注册同一套处理器,否则该独立运行模式
# 下 schema 与运行时不一致(生产入口 backend.api.app:app 借用本文件路由对象时,
# 走的是主 app 自己注册的处理器,不受这里影响)。
register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# 缓存隔离(CLAUDE.md §10.2):本文件 4 个 legacy 端点从不设置 Cache-Control,
# 独立运行模式下同样需要 app 层 default-deny 兜底(见 backend/api/cache_policy.py)。
install_cache_policy(app)


def get_readonly_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _rows_to_dicts(rows) -> list:
    return [dict(r) for r in rows]


def _valid_seasons(conn: sqlite3.Connection, league_id: int, only_finished: bool = False) -> list:
    query = "SELECT DISTINCT Season FROM dim_match WHERE League_ID = ?"
    params = [league_id]
    if only_finished:
        query += " AND status = 'Finish'"
    query += " ORDER BY Season"
    rows = conn.execute(query, params).fetchall()
    return [r["Season"] for r in rows]


def _resolve_season(conn: sqlite3.Connection, league_id: int, season: Optional[str]) -> str:
    """未显式传 season 时,默认取"最新已完赛"的赛季,而不是字符串意义上最大
    的赛季——2026/2027 未开踢赛程写入 dim_match 后,'2026/2027' 在字符串
    排序上最大,但这几个 endpoint(排名/统计)默认应该继续展示已完赛的
    2025/2026,不能因为库里多了未来赛程就悄悄把默认页切到一个还没有任何
    数据的空赛季。显式传 season(如 wdl-predictions 传 2026/2027)仍然
    按全部赛季校验,不受这条限制。"""
    all_seasons = _valid_seasons(conn, league_id, only_finished=False)
    if not all_seasons:
        raise HTTPException(
            status_code=400,
            detail={"code": "no_data_for_league", "message": f"league_id={league_id} 无数据"},
        )
    if season is None:
        finished_seasons = _valid_seasons(conn, league_id, only_finished=True)
        return (finished_seasons or all_seasons)[-1]
    if season not in all_seasons:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_season",
                "message": f"非法 season={season!r},league_id={league_id} 可用赛季: {all_seasons}",
            },
        )
    return season


def _team_i18n_map(conn: sqlite3.Connection) -> dict:
    display = team_display_map(conn)
    return {
        team_id: display_name_for_team(team_id, display=display)
        for team_id in display
    }


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


@app.get(
    "/api/league/{league_id}/overview",
    response_model=LeagueOverviewResponse,
    responses=error_responses(400, 422),
)
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
            d["crest_url"] = resolve_team_crest_url("fotmob", team_id)
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


# ── GET /api/league/{league_id}/betting ───────────────────────────────
# 2026-08-16 起不再要求任何 entitlement(除"每日精选"外全站比赛内容全部免费)。
@app.get(
    "/api/league/{league_id}/betting",
    response_model=LeagueBettingResponse,
    responses=error_responses(400, 422),
)
def league_betting(league_id: int, season: Optional[str] = None):
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
@app.get(
    "/api/league/{league_id}/matches",
    response_model=LeagueMatchesResponse,
    responses=error_responses(400, 422),
)
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
