"""单场完赛事实报告只读查询(/api/v1/matches/{id}/report,详情页四 tab 数据源)。

五张 fact 表(lineup/events/shotmap/team_stats/player_stats)的一次性投影,
单一入口 match_report():五张全空返回 None(路由据此发 available=False),
不单独维护探测函数,避免"可用性判定"与"实际取数"两套逻辑打架。

数据源约定(全部经真实库核验,见 docs/current-state.md 数据管道重建各节):
- 射门坐标:FotMob 原始数据里主客队都朝同一端(x→105)记录——30 场随机抽样
  30/30 如此,且 fotmob_client 落库不做任何坐标变换。本查询原样返回原始坐标,
  客队镜像是前端展示层的事,后端不改数据。
- 单场评分唯一真源是 fact_match_lineup.rating(fact_player_match_stats 只有
  rating_title,同一数值不同精度,不用)。
- fact_team_match_stats 的 Period='All' 行在全部完赛比赛里 100% 存在
  (13,051 场核验零缺失),本查询只取该行;extra_json 按白名单投影,
  其中 4 个恒为 null 的分组表头伪 key(shots/defense/duels/discipline)排除。
- fact_shotmap 存在 12 行 Team_ID 与主客队都对不上的来源脏数据,
  必须显式过滤,不能默认归为客队。
- 中文名走 dim_player_i18n 回退链(短名 > 全名 > 来源英文 > id),
  服务端一次建映射逐行套用,前端不碰映射表。
"""

import json
import sqlite3

from backend.queries.league_stats import _player_i18n_map

# extra_json 来源 key → DTO 字段名(白名单;§10.3 要求响应有静态 schema,
# 不能把动态 dict 原样透传)。37 个 key 是 2026-08 对真实库全量核对的结果;
# 来源新增的 key 不会自动出现在响应里——需要时在这里显式加。
TEAM_STAT_KEYS: dict[str, str] = {
    "BallPossesion": "possession",              # 来源拼写如此(少一个 s),不是笔误
    "expected_goals": "expected_goals",
    "expected_goals_open_play": "expected_goals_open_play",
    "expected_goals_set_play": "expected_goals_set_play",
    "expected_goals_non_penalty": "expected_goals_non_penalty",
    "expected_goals_on_target": "expected_goals_on_target",
    "total_shots": "total_shots",
    "ShotsOnTarget": "shots_on_target",
    "ShotsOffTarget": "shots_off_target",
    "blocked_shots": "blocked_shots",
    "shots_inside_box": "shots_inside_box",
    "shots_outside_box": "shots_outside_box",
    "shots_woodwork": "shots_woodwork",
    "big_chance": "big_chance",
    "big_chance_missed_title": "big_chance_missed",
    "touches_opp_box": "touches_opp_box",
    "passes": "passes",
    "accurate_passes": "accurate_passes",
    "own_half_passes": "own_half_passes",
    "opposition_half_passes": "opposition_half_passes",
    "long_balls_accurate": "long_balls_accurate",
    "accurate_crosses": "accurate_crosses",
    "player_throws": "player_throws",
    "Offsides": "offsides",
    "matchstats.headers.tackles": "tackles",    # 来源 key 字面带点,按字面取
    "interceptions": "interceptions",
    "shot_blocks": "shot_blocks",
    "clearances": "clearances",
    "keeper_saves": "keeper_saves",
    "duel_won": "duel_won",
    "ground_duels_won": "ground_duels_won",
    "aerials_won": "aerials_won",
    "dribbles_succeeded": "dribbles_succeeded",
    "corners": "corners",
    "fouls": "fouls",
    "yellow_cards": "yellow_cards",
    "red_cards": "red_cards",
}

# usual_position_id → 位置分组(已用 fact_player_match_stats.is_goalkeeper
# 交叉验证 0 ⇔ GK;position_id 是 11..105 的来源内部码,不外露)。
POSITION_GROUPS = {0: "GK", 1: "DEF", 2: "MID", 3: "FWD"}

# fact_match_events.extra_json 白名单(同 TEAM_STAT_KEYS 的约定:动态 dict
# 不原样透传,只显式投影已核验过的 key)。halfStrShort 全库分布:HT 13050 /
# FT 13050 / AET 6,event_type='Half' 的行里该字段无 NULL。
HALF_KINDS = frozenset({"HT", "FT", "AET"})

_PERIOD_ORDER = {
    "FirstHalf": 1, "SecondHalf": 2,
    "FirstHalfExtra": 3, "SecondHalfExtra": 4,
    "FirstExtraHalf": 3, "SecondExtraHalf": 4,   # 两种来源写法都见过,同序
    "PenaltyShootout": 5,
}


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value, digits):
    f = _to_float(value)
    return None if f is None else round(f, digits)


def _display_name(pid, source_name, i18n):
    """中文短名 > 中文全名 > 来源英文名 > id,绝不显示空白(与 league_stats 同链)。"""
    name_zh, name_zh_short = i18n.get(str(pid), (None, None)) if pid is not None else (None, None)
    return name_zh_short or name_zh or source_name or (str(pid) if pid is not None else None)


def _lineups(conn, match_id, i18n):
    rows = conn.execute(
        """SELECT Team_ID, is_home, formation, Player_ID, player_name, shirt_number,
                  usual_position_id, is_starter, is_captain, country_code, rating,
                  sub_in_time, sub_out_time,
                  json_extract(extra_json, '$.horizontalLayout.x') AS pitch_x,
                  json_extract(extra_json, '$.horizontalLayout.y') AS pitch_y
           FROM fact_match_lineup WHERE Match_ID=?""",
        (match_id,),
    ).fetchall()
    teams: dict[int, dict] = {}
    for r in rows:
        is_home = bool(r["is_home"])
        team = teams.setdefault(
            is_home,
            {"team_id": int(r["Team_ID"]), "is_home": is_home,
             "formation": r["formation"], "starters": [], "bench": []},
        )
        player = {
            "player_id": str(r["Player_ID"]),
            "name": _display_name(r["Player_ID"], r["player_name"], i18n),
            "name_en": r["player_name"],
            "shirt_number": r["shirt_number"],
            "position_group": POSITION_GROUPS.get(r["usual_position_id"]),
            "is_starter": bool(r["is_starter"]),
            "is_captain": bool(r["is_captain"]),
            "country_code": r["country_code"],
            "rating": _round(r["rating"], 1),
            "sub_in_time": r["sub_in_time"],
            "sub_out_time": r["sub_out_time"],
            "pitch_x": _round(r["pitch_x"], 3),
            "pitch_y": _round(r["pitch_y"], 3),
        }
        (team["starters"] if player["is_starter"] else team["bench"]).append(player)
    for team in teams.values():
        team["starters"].sort(key=lambda p: (p["pitch_x"] or 0, p["pitch_y"] or 0))
        team["bench"].sort(key=lambda p: (p["position_group"] or "z", p["shirt_number"] or ""))
    # 主队在前
    return [teams[k] for k in sorted(teams, reverse=True)]


def _events(conn, match_id, i18n):
    rows = conn.execute(
        """SELECT event_index, event_type, minute, overload_time, is_home,
                  home_score, away_score, player_id, player_name, card_type,
                  assist_player_id, assist_player_name,
                  sub_in_player_id, sub_in_player_name,
                  sub_out_player_id, sub_out_player_name, minutes_added,
                  extra_json
           FROM fact_match_events WHERE Match_ID=? ORDER BY event_index""",
        (match_id,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            extra = json.loads(r["extra_json"] or "{}")
        except ValueError:
            extra = {}
        half_short = extra.get("halfStrShort")
        out.append({
            "event_index": r["event_index"],
            "event_type": r["event_type"],
            "minute": r["minute"],
            "is_added_time": bool(r["overload_time"]),
            "minutes_added": r["minutes_added"],
            "is_home": None if r["is_home"] is None else bool(r["is_home"]),
            "home_score": r["home_score"],
            "away_score": r["away_score"],
            "player_name": _display_name(r["player_id"], r["player_name"], i18n),
            "card_type": r["card_type"],
            "assist_player_name": _display_name(
                r["assist_player_id"], r["assist_player_name"], i18n),
            "sub_in_player_name": _display_name(
                r["sub_in_player_id"], r["sub_in_player_name"], i18n),
            "sub_out_player_name": _display_name(
                r["sub_out_player_id"], r["sub_out_player_name"], i18n),
            "half_kind": half_short if half_short in HALF_KINDS else None,
            # 乌龙球判据只认顶层 ownGoal:全库 1079 条 ownGoal=true 的事件里
            # 13 条没有 shotmapEvent、2 条 shotmapEvent.isOwnGoal 与顶层矛盾;
            # 反向(ownGoal 空但 shotmapEvent.isOwnGoal=true)漏报 0 条。
            # 不要改用 shotmapEvent.isOwnGoal。
            "is_own_goal": extra.get("ownGoal") is True,
        })
    return out


def _own_goal_fields(r) -> dict:
    """乌龙球两字段(is_own_goal / is_own_goal_inferred)的统一出口。

    优先级:采集值(Is_Own_Goal 非 NULL,2026-08-24 起新抓的场次才有)→
    推断(xG IS NULL AND Outcome='Goal';全库 1022 条 NULL-xG 射门 100% 是
    乌龙球,avg X_Coord=5.34 即本方球门端,并与 fact_match_events 的
    extra_json.ownGoal 交叉印证)。inferred 标志让前端能对推断值降级表述,
    不把推断当采集确证。非进球射门 xG 缺失(理论上不存在,防御)不推断。"""
    collected = r["Is_Own_Goal"]
    if collected is not None:
        return {"is_own_goal": bool(collected), "is_own_goal_inferred": False}
    inferred = r["xG"] is None and r["Outcome"] == "Goal"
    return {"is_own_goal": inferred, "is_own_goal_inferred": inferred}


def _shots(conn, match_id, home_team_id, away_team_id, lineup_names, i18n):
    rows = conn.execute(
        """SELECT Player_ID, Team_ID, Minute, Period, X_Coord, Y_Coord,
                  xG, xGOT, Situation, Outcome, Shot_Type,
                  Is_Blocked, Is_On_Target, Shot_ID, Blocked_X, Blocked_Y,
                  Goal_Crossed_Y, Goal_Crossed_Z, On_Goal_Shot_X,
                  On_Goal_Shot_Y, On_Goal_Shot_Zoom_Ratio, Is_Own_Goal
           FROM fact_shotmap
           WHERE Match_ID=? AND Team_ID IN (?, ?)""",
        (match_id, home_team_id, away_team_id),
    ).fetchall()
    out = []
    for r in rows:
        pid = str(r["Player_ID"])
        # Is_Blocked/Is_On_Target 2026-08-23 起才开始采集(见
        # backend/migrations/core/0005_shotmap_raw_fields.sql),旧场次/
        # 未回填的场次这两列恒为 NULL——前端必须按 is_blocked is None 判定
        # "这场没有精确口径可用",不能把 NULL 当 False。
        out.append({
            "player_id": pid,
            "player_name": _display_name(r["Player_ID"], lineup_names.get(pid), i18n),
            "team_id": int(r["Team_ID"]),
            "is_home": int(r["Team_ID"]) == home_team_id,
            "minute": r["Minute"],
            "period": r["Period"],
            "x": _round(r["X_Coord"], 2),
            "y": _round(r["Y_Coord"], 2),
            "xg": _round(r["xG"], 3),
            "xgot": _round(r["xGOT"], 3),
            "situation": r["Situation"],
            "outcome": r["Outcome"],
            "shot_type": r["Shot_Type"],
            "is_blocked": bool(r["Is_Blocked"]) if r["Is_Blocked"] is not None else None,
            "is_on_target": bool(r["Is_On_Target"]) if r["Is_On_Target"] is not None else None,
            # 2026-08-24 起才采集,画射门轨迹线用——原样透传,不做镜像/换算
            # (与 x/y 同一架构约定,见 MatchReportShot 字段注释)。
            "shot_id": r["Shot_ID"],
            "blocked_x": _round(r["Blocked_X"], 2),
            "blocked_y": _round(r["Blocked_Y"], 2),
            "goal_crossed_y": _round(r["Goal_Crossed_Y"], 2),
            "goal_crossed_z": _round(r["Goal_Crossed_Z"], 2),
            "on_goal_shot_x": _round(r["On_Goal_Shot_X"], 2),
            "on_goal_shot_y": _round(r["On_Goal_Shot_Y"], 2),
            "on_goal_shot_zoom_ratio": _round(r["On_Goal_Shot_Zoom_Ratio"], 2),
            # 乌龙球(2026-08-24,migrations/core/0010)。FotMob 把乌龙球记在
            # "打进自家球门那一队"名下,前端按受益方(对方)计进球数,否则
            # 分队比分归错队(对照实验 400 场含乌龙球比赛错 392 场)。
            # Is_Own_Goal 采集值缺失(历史行)时按"xG IS NULL 且 Outcome=
            # 'Goal'"推断——该判据全库 1022/1022 命中且有 fact_match_events
            # 的 ownGoal 标记交叉印证;推断值必须与采集值可区分,所以配一个
            # is_own_goal_inferred 标志,不冒充确证(CLAUDE.md §2.2)。
            **_own_goal_fields(r),
        })
    out.sort(key=lambda s: (_PERIOD_ORDER.get(s["period"], 9), s["minute"] or 0))
    return out


def _team_stats_rows(conn, match_id, home_team_id, periods):
    ph = ",".join("?" for _ in periods)
    rows = conn.execute(
        f"""SELECT Team_ID, Period, Goals, extra_json FROM fact_team_match_stats
             WHERE Match_ID=? AND Period IN ({ph})""",
        (match_id, *periods),
    ).fetchall()
    out = []
    for r in rows:
        try:
            extra = json.loads(r["extra_json"] or "{}")
        except ValueError:
            extra = {}
        stat = {
            "team_id": int(r["Team_ID"]),
            "is_home": int(r["Team_ID"]) == home_team_id,
            "period": r["Period"],
            "goals": _to_float(r["Goals"]),
        }
        for src_key, field in TEAM_STAT_KEYS.items():
            stat[field] = _to_float(extra.get(src_key))
        out.append(stat)
    out.sort(key=lambda s: (s["period"] != "All", not s["is_home"]))
    return out


def _team_stats(conn, match_id, home_team_id):
    """全场(Period='All')——team_stats 字段的既有口径,不受阶段 2 影响。"""
    return _team_stats_rows(conn, match_id, home_team_id, ("All",))


def _team_stats_by_half(conn, match_id, home_team_id):
    """2026-08-23:上/下半场分段,数据已在 fact_team_match_stats 里
    (FirstHalf 19,124 行 / SecondHalf 19,122 行,查询层此前写死只取
    'All')。该场没有半场行时返回空列表,前端据此决定是否显示切换器。"""
    return _team_stats_rows(conn, match_id, home_team_id, ("FirstHalf", "SecondHalf"))


def _player_stats(conn, match_id, home_team_id, ratings, i18n):
    rows = conn.execute(
        """SELECT Player_ID, Team_ID, is_goalkeeper, minutes_played, player_name,
                  goals, assists, expected_goals, expected_assists,
                  ShotsOnTarget, ShotsOffTarget, accurate_passes, chances_created,
                  touches, touches_opp_box, dribbles_succeeded,
                  "matchstats.headers.tackles" AS tackles,
                  clearances, interceptions, duel_won, aerials_won, fouls,
                  corners, Offsides, saves, goals_conceded, goals_prevented,
                  passes_into_final_third, long_balls_accurate, dispossessed,
                  shot_blocks, recoveries, dribbled_past, defensive_actions,
                  duel_lost, ground_duels_won, was_fouled,
                  expected_goals_on_target_faced, keeper_diving_save,
                  saves_inside_box, keeper_sweeper, punches, keeper_high_claim,
                  accurate_passes_total,
                  physical_metrics_topspeed, physical_metrics_distance_covered,
                  physical_metrics_walking, physical_metrics_jogging,
                  physical_metrics_running, physical_metrics_sprinting,
                  physical_metrics_number_of_sprints
           FROM fact_player_match_stats WHERE Match_ID=?""",
        (match_id,),
    ).fetchall()
    out = []
    for r in rows:
        pid = str(r["Player_ID"])
        out.append({
            "player_id": pid,
            "name": _display_name(r["Player_ID"], r["player_name"], i18n),
            "team_id": int(r["Team_ID"]),
            "is_home": int(r["Team_ID"]) == home_team_id,
            "is_goalkeeper": bool(r["is_goalkeeper"]),
            "minutes_played": _to_float(r["minutes_played"]),
            # 评分来自 fact_match_lineup.rating(唯一真源),不取本表 rating_title
            "rating": ratings.get(pid),
            "goals": _to_float(r["goals"]),
            "assists": _to_float(r["assists"]),
            "expected_goals": _round(r["expected_goals"], 3),
            "expected_assists": _round(r["expected_assists"], 3),
            "shots_on_target": _to_float(r["ShotsOnTarget"]),
            "shots_off_target": _to_float(r["ShotsOffTarget"]),
            "accurate_passes": _to_float(r["accurate_passes"]),
            "chances_created": _to_float(r["chances_created"]),
            "touches": _to_float(r["touches"]),
            "touches_opp_box": _to_float(r["touches_opp_box"]),
            "dribbles_succeeded": _to_float(r["dribbles_succeeded"]),
            "tackles": _to_float(r["tackles"]),
            "clearances": _to_float(r["clearances"]),
            "interceptions": _to_float(r["interceptions"]),
            "duel_won": _to_float(r["duel_won"]),
            "aerials_won": _to_float(r["aerials_won"]),
            "fouls": _to_float(r["fouls"]),
            "corners": _to_float(r["corners"]),
            "offsides": _to_float(r["Offsides"]),
            "saves": _to_float(r["saves"]),
            "goals_conceded": _to_float(r["goals_conceded"]),
            "goals_prevented": _round(r["goals_prevented"], 3),
            # 2026-08-23 对照 FotMob 官方安卓包补充投影(此前已采集但从未
            # 下发)——按 FotMob 的球员分组(进攻/防守/对抗/门将)补齐高覆盖
            # 字段,详见 docs/current-state.md 或本次调研 plan 里逐字段的
            # 近赛季非空率实测。
            "passes_into_final_third": _to_float(r["passes_into_final_third"]),
            "long_balls_accurate": _to_float(r["long_balls_accurate"]),
            "dispossessed": _to_float(r["dispossessed"]),
            "shot_blocks": _to_float(r["shot_blocks"]),
            "recoveries": _to_float(r["recoveries"]),
            "dribbled_past": _to_float(r["dribbled_past"]),
            "defensive_actions": _to_float(r["defensive_actions"]),
            "duel_lost": _to_float(r["duel_lost"]),
            "ground_duels_won": _to_float(r["ground_duels_won"]),
            "was_fouled": _to_float(r["was_fouled"]),
            "expected_goals_on_target_faced": _round(r["expected_goals_on_target_faced"], 3),
            "keeper_diving_save": _to_float(r["keeper_diving_save"]),
            "saves_inside_box": _to_float(r["saves_inside_box"]),
            "keeper_sweeper": _to_float(r["keeper_sweeper"]),
            "punches": _to_float(r["punches"]),
            "keeper_high_claim": _to_float(r["keeper_high_claim"]),
            "accurate_passes_total": _to_float(r["accurate_passes_total"]),
            # 体能(阶段 6):payload 没有该球员这一组时全部为 None,前端
            # 按"至少一项非空才渲染体能行"处理(有则显示、无则不显示,
            # 与 FotMob 自己的行为一致)。覆盖率现实见 fotmob_client.py
            # 里 physical_metrics_* 字段旁的实测注释。
            "physical_metrics_topspeed": _to_float(r["physical_metrics_topspeed"]),
            "physical_metrics_distance_covered": _to_float(r["physical_metrics_distance_covered"]),
            "physical_metrics_walking": _to_float(r["physical_metrics_walking"]),
            "physical_metrics_jogging": _to_float(r["physical_metrics_jogging"]),
            "physical_metrics_running": _to_float(r["physical_metrics_running"]),
            "physical_metrics_sprinting": _to_float(r["physical_metrics_sprinting"]),
            "physical_metrics_number_of_sprints": _to_float(r["physical_metrics_number_of_sprints"]),
        })
    out.sort(key=lambda p: (not p["is_home"], -(p["rating"] or 0)))
    return out


def _top_rated(lineups) -> dict | None:
    """全场评分最高的一名球员(2026-08-24,总览「最高评分」卡)。

    口径:fact_match_lineup.rating(FotMob 球员评分,与阵容/球员表同一来源,
    不另起第二套评分)。库里没有 FotMob 的 isPlayerOfTheMatch 官方标志,
    所以标题语义是「最高评分」(FotMob 自己的 top_rated 口径),不冒充官方
    MOTM 评选(站长 2026-08-24 拍板)。并列最高分时取 player_id 字典序较小
    者——确定性优先,不引入随机。全场无评分返回 None,前端整卡不渲染。"""
    best = None
    for team in lineups:
        for p in team["starters"] + team["bench"]:
            if p["rating"] is None:
                continue
            key = (-p["rating"], p["player_id"])
            if best is None or key < best[0]:
                best = (key, p, team)
    if best is None:
        return None
    _, p, team = best
    return {
        "player_id": p["player_id"],
        "name": p["name"],
        "team_id": team["team_id"],
        "is_home": team["is_home"],
        "rating": p["rating"],
        "shirt_number": p["shirt_number"],
    }


def _momentum(conn, match_id):
    """势头曲线(逐分钟,FotMob 自己的黑箱综合评分,见
    backend/fotmob_client.py::parse_momentum_records 的口径说明)。2026-08-23
    起才采集,旧场次/未重新抓取的场次没有行——空列表如实表示"这场没有势头
    数据",不是"这场没有势头这个概念"。"""
    rows = conn.execute(
        "SELECT Minute, Value FROM fact_match_momentum WHERE Match_ID=? ORDER BY Minute",
        (match_id,),
    ).fetchall()
    return [{"minute": r["Minute"], "value": r["Value"]} for r in rows]


def match_report(conn: sqlite3.Connection, match_id: int) -> dict | None:
    """五张事实表的一次性只读投影;五张全空 → None(未完赛或未入库)。"""
    m = conn.execute(
        "SELECT Home_Team_ID, Away_Team_ID FROM dim_match WHERE Match_ID=?",
        (match_id,),
    ).fetchone()
    if m is None:
        return None
    home_id, away_id = int(m["Home_Team_ID"]), int(m["Away_Team_ID"])

    i18n = _player_i18n_map(conn)
    lineups = _lineups(conn, match_id, i18n)
    # 射门表没有球员名,回退链的"来源英文名"一档从阵容行取
    lineup_names = {
        p["player_id"]: p["name_en"]
        for team in lineups for p in team["starters"] + team["bench"]
    }
    ratings = {
        p["player_id"]: p["rating"]
        for team in lineups for p in team["starters"] + team["bench"]
        if p["rating"] is not None
    }
    events = _events(conn, match_id, i18n)
    shots = _shots(conn, match_id, home_id, away_id, lineup_names, i18n)
    team_stats = _team_stats(conn, match_id, home_id)
    team_stats_by_half = _team_stats_by_half(conn, match_id, home_id)
    player_stats = _player_stats(conn, match_id, home_id, ratings, i18n)
    momentum = _momentum(conn, match_id)

    coverage = {
        "lineup": bool(lineups),
        "events": bool(events),
        "shots": bool(shots),
        "team_stats": bool(team_stats),
        "player_stats": bool(player_stats),
    }
    # 势头数据单独统计覆盖率(方便观测采集进度),但不参与"这场比赛有没有
    # 可展示内容"的判定——势头曲线是补充性的黑箱评分,不能单独撑起一份
    # "可用"的比赛报告(同一场比赛五张核心事实表全空、只有势头数据这种
    # 情况理论上不该发生,不给它单独当"有数据"的资格)。
    if not any(coverage.values()):
        return None
    coverage["momentum"] = bool(momentum)
    return {
        "coverage": coverage,
        "lineups": lineups,
        "events": events,
        "shots": shots,
        "team_stats": team_stats,
        "team_stats_by_half": team_stats_by_half,
        "player_stats": player_stats,
        "momentum": momentum,
        "top_rated": _top_rated(lineups),
    }
