"""
api_server.py — FastAPI serving 层(ROADMAP.md Phase 1.3)。

前端只读 API,不直连 DB(CLAUDE.md §2)。本文件是前端与数据之间的唯一通道。

免费 / 付费边界按字段级分级(CLAUDE.md §3),gate 落在 endpoint 层:
    - /api/league/{league_id}/overview        — 免费,不调用 require_membership。
    - /api/league/{league_id}/betting         — 付费,调用 require_membership 占位。
    - /api/league/{league_id}/matches         — 免费(赛程/结果)。
    - /api/league/{league_id}/wdl-predictions — 付费核心(CLAUDE.md §3 模型概率)。
      每场先过"7 天有效期"闸门(availability,用服务端 datetime.now() 动态
      算,不写死日期),再叠加付费 gate,两件事分开判断、不要混在一起:
        - availability='upcoming'(距开赛 >7 天):不管付费与否,概率/倾向
          一律不下发(p_home/p_draw/p_away/tendency 都不放进响应体)——
          这时候"还没到能看的时候",不是"氪不氪金"的问题。
        - availability='live'(距开赛 ≤7 天):走原有付费 gate——未付费只
          返回 tendency + confidence + locked:true,精确概率 p_home/
          p_draw/p_away 根本不放进响应体;已付费额外带上精确概率。
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
from datetime import datetime
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
    # 现在还没有真实登录/支付,用查询参数 ?simulate_membership=paid 模拟两态,
    # 方便验证"服务端按这个返回值真的分支"这件事本身——真正接入 1.6 后只改
    # 这个函数体(读真实 subscription 状态),每个付费 endpoint 的调用方式
    # 不用跟着改。默认(不传参数)按未付费处理,不默认放行。
    return request.query_params.get("simulate_membership") == "paid"


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
        raise HTTPException(status_code=400, detail=f"league_id={league_id} 无数据")
    if season is None:
        finished_seasons = _valid_seasons(conn, league_id, only_finished=True)
        return (finished_seasons or all_seasons)[-1]
    if season not in all_seasons:
        raise HTTPException(
            status_code=400,
            detail=f"非法 season={season!r},league_id={league_id} 可用赛季: {all_seasons}",
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


# ── 付费核心:GET /api/league/{league_id}/wdl-predictions ─────────────
def _resolve_prediction_season(conn: sqlite3.Connection, league_id: int, season: Optional[str]) -> str:
    rows = conn.execute(
        "SELECT DISTINCT season FROM gold_wdl_predictions WHERE league_id = ? ORDER BY season",
        (league_id,),
    ).fetchall()
    seasons = [r["season"] for r in rows]
    if not seasons:
        raise HTTPException(status_code=400, detail=f"league_id={league_id} 没有任何 WDL 预测数据")
    if season is None:
        return seasons[-1]
    if season not in seasons:
        raise HTTPException(
            status_code=400,
            detail=f"非法 season={season!r},league_id={league_id} 可用预测赛季: {seasons}",
        )
    return season


WDL_LIVE_WINDOW_DAYS = 7


def _wdl_availability(match_date_str: Optional[str]) -> tuple:
    """distance-to-kickoff 用服务端当前时间动态算(datetime.now()),不写死
    任何日期——"7 天有效期"这条线每次请求都是新算的,过了明天这条线本身
    也会跟着往后移一天。"""
    if not match_date_str:
        return "upcoming", None
    match_date = datetime.strptime(match_date_str, "%Y-%m-%d").date()
    days_until = (match_date - datetime.now().date()).days
    availability = "live" if days_until <= WDL_LIVE_WINDOW_DAYS else "upcoming"
    return availability, days_until


@app.get("/api/league/{league_id}/wdl-predictions")
def league_wdl_predictions(request: Request, league_id: int, season: Optional[str] = None):
    is_paid = require_membership(request)

    conn = get_readonly_connection()
    try:
        season = _resolve_prediction_season(conn, league_id, season)
        team_zh = _team_i18n_map(conn)

        rows = conn.execute(
            """
            SELECT g.match_id, g.p_home, g.p_draw, g.p_away, g.confidence, g.reason,
                   m.Date, m.Match_Round, m.Home_Team_ID, m.Away_Team_ID, m.status
            FROM gold_wdl_predictions g
            JOIN dim_match m ON g.match_id = m.Match_ID
            WHERE g.league_id = ? AND g.season = ?
            ORDER BY m.Date, g.match_id
            """,
            (league_id, season),
        ).fetchall()

        matches = []
        for r in rows:
            d = dict(r)
            home_id, away_id = d["Home_Team_ID"], d["Away_Team_ID"]
            p_home, p_draw, p_away = d["p_home"], d["p_draw"], d["p_away"]
            availability, days_until = _wdl_availability(d["Date"])

            entry = {
                "match_id": d["match_id"],
                "date": d["Date"],
                "round": d["Match_Round"],
                "status": d["status"],
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_team_name_zh": team_zh.get(home_id),
                "away_team_name_zh": team_zh.get(away_id),
                "availability": availability,
                "days_until_kickoff": days_until,
            }

            # 🔴 'upcoming'(距开赛 >7 天):不管付费与否,概率/倾向一律不
            # 下发——p_home/p_draw/p_away/tendency/confidence/reason/locked
            # 这些 key 在这一分支根本不存在,不是"付费判断"这条线管的事。
            if availability == "live":
                tendency = None
                if p_home is not None and p_draw is not None and p_away is not None:
                    probs = {"home": p_home, "draw": p_draw, "away": p_away}
                    tendency = max(probs, key=probs.get)

                entry["tendency"] = tendency
                entry["confidence"] = d["confidence"]
                entry["reason"] = d["reason"]
                entry["locked"] = not is_paid
                # 精确概率只在已付费时才放进响应体;未付费时这三个 key
                # 根本不存在(不是放了 null,更不是放了真值让前端自己藏)。
                if is_paid:
                    entry["p_home"] = p_home
                    entry["p_draw"] = p_draw
                    entry["p_away"] = p_away

            matches.append(entry)

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
