"""联赛球员象限图(2026-09-15):`/api/v1/leagues/{id}/player-quadrant`。

一次返回全部位置 × 全部指标(不接受 position 参数)——真正的出场占比门槛
(40%)留在前端,与 hiddenNote() 那套"藏了几个、为什么"的诚实披露共用同一份
数据(如果后端先按位置/门槛切好,前端就数不出"筛选后还剩几个人参与均值"
这件事)。后端只按宽松下限 minutes_share>=0.15 裁掉真正的长尾(几分钟替补),
并把裁掉几个人如实回传,不是静默丢弃。
"""

from __future__ import annotations

import sqlite3

from backend.metrics.registry import get_metric
from backend.queries.leagues import CROSS_LEAGUE_LEAGUE_IDS
from backend.queries.matches import _team_ref
from backend.queries.teams import team_brand_color_map, team_display_map

# 后端裁长尾的宽松下限——远低于前端实际使用的 40% 门槛,只是防止把几分钟
# 出场的替补也塞进 payload(徒增体积,这些人前端门槛也会挡掉)。
MINUTES_SHARE_FLOOR = 0.15

# 与 backend/queries/league_stats.py::MIN_MATCHES_FOR_SEASON_DATA 同一理由:
# 赛季刚开踢时不展示"几乎全是单场波动"的假整季榜。球员数据比球队数据噪声
# 更大(单个球员的样本天然比球队小),沿用同一个门槛,不单独发明一个数字。
MIN_MATCHES_FOR_SEASON_DATA = 4


def _player_i18n_map(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute(
            "SELECT Player_ID, name_zh, name_zh_short FROM dim_player_i18n"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(r["Player_ID"]): (r["name_zh"], r["name_zh_short"]) for r in rows}


def _seasons_of(conn: sqlite3.Connection, league_id: int) -> list[str]:
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT Season FROM silver_player_season WHERE League_ID=? ORDER BY Season",
                (league_id,),
            )
        ]
    except sqlite3.OperationalError:
        return []


def _resolve_season(seasons: list[str], season: str | None) -> str | None:
    if not seasons:
        return season
    if season is not None and season in seasons:
        return season
    return seasons[-1]


# 与 backend/silver/player_season.py::PLAYER_METRIC_SPECS 的 metric_key 集合
# 必须一致(test_player_quadrant_api.py 断言这条)。
RATIO_METRIC_KEYS = (
    "npxg_per90",
    "finishing_delta_per90",
    "chances_created_per90",
    "xa_per90",
    "defensive_actions_per90",
    "duel_win_rate",
    "touches_per90",
    "progression_rate",
    "xgot_faced_per90",
    "goals_prevented_per90",
    "pass_completion_rate",
    "long_ball_share",
)


def _ratios_by_player(conn: sqlite3.Connection, league_id: int, season: str) -> dict:
    rows = conn.execute(
        """SELECT Player_ID, metric_key, numerator_sum, denominator_sum, paired_matches
           FROM silver_player_season_ratios
           WHERE League_ID=? AND Season=?""",
        (league_id, season),
    ).fetchall()
    out: dict = {}
    for r in rows:
        player_id, metric_key, num, den, paired = (
            r["Player_ID"], r["metric_key"], r["numerator_sum"], r["denominator_sum"], r["paired_matches"],
        )
        if num is None or den is None or den <= 0:
            value = None
        else:
            scale = get_metric(metric_key).display_scale
            value = scale * num / den
        out.setdefault(str(player_id), {})[metric_key] = {
            "value": value,
            "numerator": num,
            "denominator": den,
            "paired_matches": paired or 0,
        }
    return out


def player_quadrant_stats(
    conn: sqlite3.Connection, league_id: int, season: str | None = None
) -> dict:
    seasons = _seasons_of(conn, league_id)
    if not seasons:
        return {"league_id": league_id, "season": season, "available_seasons": [], "rows": []}

    season = _resolve_season(seasons, season)

    if league_id not in CROSS_LEAGUE_LEAGUE_IDS:
        max_apps = conn.execute(
            "SELECT MAX(COALESCE(appearances, 0)) FROM silver_player_season WHERE League_ID=? AND Season=?",
            (league_id, season),
        ).fetchone()[0] or 0
        if max_apps < MIN_MATCHES_FOR_SEASON_DATA:
            return {
                "league_id": league_id,
                "season": season,
                "available_seasons": seasons,
                "rows": [],
                "empty_reason": "本赛季刚开始，暂无足够数据（需至少一名球员出场 4 场）",
            }

    display = team_display_map(conn)
    colors = team_brand_color_map(conn, league_id, season)
    player_zh = _player_i18n_map(conn)
    ratios_by_player = _ratios_by_player(conn, league_id, season)

    all_rows = conn.execute(
        """SELECT Player_ID, Team_ID, player_name, usual_position, appearances,
                  minutes_played, team_minutes, minutes_share, teams_count
           FROM silver_player_season
           WHERE League_ID=? AND Season=?""",
        (league_id, season),
    ).fetchall()

    kept = []
    excluded = 0
    for r in all_rows:
        share = r["minutes_share"]
        if share is None or share < MINUTES_SHARE_FLOOR:
            excluded += 1
            continue
        kept.append(r)

    rows = []
    for r in kept:
        pid = str(r["Player_ID"])
        name_zh, name_zh_short = player_zh.get(pid, (None, None))
        team_id = r["Team_ID"]
        rows.append(
            {
                "player": {
                    "player_id": pid,
                    "name": name_zh_short or name_zh or r["player_name"] or pid,
                    "name_en": r["player_name"],
                },
                "team": _team_ref(team_id, None, display),
                "team_color": colors.get(int(team_id)) if team_id is not None else None,
                "usual_position": r["usual_position"],
                "appearances": r["appearances"],
                "minutes_played": r["minutes_played"],
                "team_minutes": r["team_minutes"],
                "minutes_share": r["minutes_share"],
                "teams_count": r["teams_count"],
                "ratios": ratios_by_player.get(pid),
            }
        )

    return {
        "league_id": league_id,
        "season": season,
        "available_seasons": seasons,
        "rows": rows,
        "excluded_below_floor": excluded,
    }
