"""联赛级球队/球员赛季统计只读查询(/api/v1/leagues/{id}/team-stats、/players)。

字段纪律(CLAUDE.md §3):
- 球队统计只 SELECT 免费字段(射门/射正/控球/xG/xGOT)。角球/红黄牌/零封/BTTS
  是付费深度报告字段,物理上不进本查询的 SQL,更不进响应——不是取了再藏。
- 球员榜只暴露 5 个免费维度(进球/助攻/xG/xGOT/评分),各取 top 10。

赛季解析与 queries/matches.standings 同规则:available_seasons 来自数据源表
本身;请求的 season 不在列表时回退最新赛季(响应如实返回实际使用的赛季)。
"""

import sqlite3

from backend.queries.matches import _team_ref
from backend.queries.teams import team_display_map

# 免费球员榜维度(与 legacy /api/league/{id}/overview 的 FREE_PLAYER_STATS 同集合)
FREE_PLAYER_BOARDS: list[tuple[str, str]] = [
    ("goals", "进球"),
    ("goal_assist", "助攻"),
    ("expected_goals", "xG"),
    ("expected_goalsontarget", "xGOT"),
    ("rating", "评分"),
]

_BOARD_TOP_N = 10


def _seasons_of(conn: sqlite3.Connection, table: str, league_id: int) -> list[str]:
    try:
        return [
            r[0]
            for r in conn.execute(
                f"SELECT DISTINCT Season FROM {table} WHERE League_ID=? ORDER BY Season",
                (league_id,),
            )
        ]
    except sqlite3.OperationalError:
        return []


def _resolve_season(seasons: list[str], season: str | None) -> str | None:
    if not seasons:
        return season
    if season is None or season not in seasons:
        return seasons[-1]
    return season


def team_season_stats(
    conn: sqlite3.Connection, league_id: int, season: str | None = None
) -> dict:
    seasons = _seasons_of(conn, "silver_team_season_stats", league_id)
    if not seasons:
        return {"season": season, "available_seasons": [], "rows": []}
    season = _resolve_season(seasons, season)
    display = team_display_map(conn)
    rows = conn.execute(
        """SELECT Team_ID, matches_played, avg_total_shots, avg_shots_on_target,
                  avg_possession, avg_expected_goals, avg_expected_goals_on_target
           FROM silver_team_season_stats
           WHERE League_ID=? AND Season=?
           ORDER BY Team_ID""",
        (league_id, season),
    ).fetchall()
    return {
        "season": season,
        "available_seasons": seasons,
        "rows": [
            {
                "team": _team_ref(r["Team_ID"], None, display),
                "matches_played": r["matches_played"],
                "avg_total_shots": r["avg_total_shots"],
                "avg_shots_on_target": r["avg_shots_on_target"],
                "avg_possession": r["avg_possession"],
                "avg_expected_goals": r["avg_expected_goals"],
                "avg_expected_goals_on_target": r["avg_expected_goals_on_target"],
            }
            for r in rows
        ],
    }


def _player_i18n_map(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute(
            "SELECT Player_ID, name_zh, name_zh_short FROM dim_player_i18n"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(r["Player_ID"]): (r["name_zh"], r["name_zh_short"]) for r in rows}


def player_leaderboards(
    conn: sqlite3.Connection, league_id: int, season: str | None = None
) -> dict:
    seasons = _seasons_of(conn, "fact_season_player_stats", league_id)
    if not seasons:
        return {"season": season, "available_seasons": [], "boards": []}
    season = _resolve_season(seasons, season)
    display = team_display_map(conn)
    player_zh = _player_i18n_map(conn)

    boards = []
    for stat_name, label_zh in FREE_PLAYER_BOARDS:
        rows = conn.execute(
            """SELECT Player_ID, Player_Name, Team_ID, Team_Name, rank, value
               FROM fact_season_player_stats
               WHERE League_ID=? AND Season=? AND stat_name=?
               ORDER BY rank LIMIT ?""",
            (league_id, season, stat_name, _BOARD_TOP_N),
        ).fetchall()
        entries = []
        for r in rows:
            pid = str(r["Player_ID"])
            name_zh, name_zh_short = player_zh.get(pid, (None, None))
            entries.append(
                {
                    "player_id": pid,
                    # 中文短名 > 中文全名 > 来源英文名 > id,绝不显示空白
                    "name": name_zh_short or name_zh or r["Player_Name"] or pid,
                    "name_en": r["Player_Name"],
                    "team": _team_ref(r["Team_ID"], r["Team_Name"], display),
                    "rank": r["rank"],
                    "value": r["value"],
                }
            )
        boards.append({"stat_name": stat_name, "label_zh": label_zh, "entries": entries})

    return {"season": season, "available_seasons": seasons, "boards": boards}
