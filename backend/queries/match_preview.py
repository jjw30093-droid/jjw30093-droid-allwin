"""赛前预览聚合(/api/v1/matches/{id}/preview,详情页"数据"tab 阵容/风格/球员子tab)。

单一入口 build_match_preview():把 lineup_preview(阵容/伤停快照)、
team_style_preview(风格象限 + 进攻来源)、attack_chain(图1 进攻转化链,
Phase 2.1,唯一走 window.py 分级窗口的图表查询)、player_form(关键球员
占比 + 门将对位)几个查询模块的结果拼成一份响应,路由层不做任何数据
组装(§10.3 薄路由)。

各模块独立降级,没有全局 available=False 开关:
- 两队都没有阵容快照时 lineups.home/away 为 None(不是空侧结构),
  区分"这场还没开始被采集"和"采集了但阵容尚未公布"(lineup_preview 的既有约定);
- home_window/away_window 缺失(该队在这个联赛还没打过历史比赛)时为 None;
- style_views/attack_sources/attack_chains/key_players/keepers 各自可能是
  空列表或字段级 None,由调用方(前端)按各模块自己的规则展示"数据不足"
  而不是整体报错。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from backend.db.util import utc_now_iso
from backend.queries import (
    attack_chain,
    defensive_pressure,
    lineup_preview,
    matchup,
    player_form,
    possession_control,
    team_style_preview,
)


def build_match_preview(
    conn_core: sqlite3.Connection, conn_odds: sqlite3.Connection, match: dict,
) -> dict[str, Any]:
    match_id = match["match_id"]
    home_id = match["home"]["team_id"]
    away_id = match["away"]["team_id"]
    league_id = match["league_id"]
    # 窗口边界优先用精确开球时刻(CLAUDE.md §6.2.1),缺失时降级到自然日——
    # 下游查询统一用 COALESCE(kickoff_at_utc, Date) 比较,两种边界值格式都
    # 兼容(ISO 时间戳按字符串比较与按时间比较结果一致)。
    before_date = match.get("kickoff_at_utc") or match["date_utc"]

    lineup = lineup_preview.latest_lineup(conn_odds, conn_core, match_id)

    return {
        "match_id": match_id,
        "observed_at": utc_now_iso(),
        "home_window": team_style_preview.team_window_bounds(conn_core, home_id, league_id, before_date),
        "away_window": team_style_preview.team_window_bounds(conn_core, away_id, league_id, before_date),
        "lineups": {
            "lineup_type": lineup["lineup_type"] if lineup else None,
            "source": lineup["source"] if lineup else None,
            "observed_at": lineup["observed_at"] if lineup else None,
            "home": lineup["home"] if lineup else None,
            "away": lineup["away"] if lineup else None,
        },
        "sidelined": {
            "home": lineup_preview.latest_sidelined_for_team(conn_odds, conn_core, match_id, home_id),
            "away": lineup_preview.latest_sidelined_for_team(conn_odds, conn_core, match_id, away_id),
        },
        "style_views": team_style_preview.league_style_views(conn_core, league_id, before_date),
        "attack_sources": {
            "home": team_style_preview.team_attack_sources(conn_core, home_id, league_id, before_date),
            "away": team_style_preview.team_attack_sources(conn_core, away_id, league_id, before_date),
        },
        "attack_chains": {
            "home": attack_chain.team_attack_chain(conn_core, home_id, league_id, before_date, is_home=True),
            "away": attack_chain.team_attack_chain(conn_core, away_id, league_id, before_date, is_home=False),
        },
        "possession_controls": {
            "home": possession_control.team_possession_control(conn_core, home_id, league_id, before_date, is_home=True),
            "away": possession_control.team_possession_control(conn_core, away_id, league_id, before_date, is_home=False),
        },
        "defensive_pressures": {
            "home": defensive_pressure.team_defensive_pressure(conn_core, home_id, league_id, before_date, is_home=True),
            "away": defensive_pressure.team_defensive_pressure(conn_core, away_id, league_id, before_date, is_home=False),
        },
        "matchup_profiles": {
            "home": matchup.team_matchup_profile(conn_core, home_id, league_id, before_date, is_home=True),
            "away": matchup.team_matchup_profile(conn_core, away_id, league_id, before_date, is_home=False),
        },
        "key_players": {
            "home": player_form.team_key_players(conn_core, home_id, league_id, before_date),
            "away": player_form.team_key_players(conn_core, away_id, league_id, before_date),
        },
        "keepers": {
            "home": player_form.team_goalkeepers(conn_core, home_id, league_id, before_date),
            "away": player_form.team_goalkeepers(conn_core, away_id, league_id, before_date),
        },
    }
