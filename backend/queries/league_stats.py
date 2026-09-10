"""联赛级球队/球员赛季统计只读查询(/api/v1/leagues/{id}/team-stats、/players)。

字段纪律(2026-08-16 产品权限口径修正,经用户批准):除"每日精选"外全站
比赛内容全部免费,包括匿名——球队赛季统计与球员榜恒全字段投影,不再区分
免费/付费深度字段。角球/红黄牌/零封/BTTS 此前被排除在 SQL 之外只是历史
付费墙的残留,现在与射门/射正/控球/xG/xGOT 同属免费内容。

赛季解析与 queries/matches.standings 同规则:available_seasons 来自数据源表
本身;请求的 season 不在列表时回退最新赛季(响应如实返回实际使用的赛季)。
"""

import json
import sqlite3

from backend.queries.matches import _team_ref
from backend.queries.teams import team_display_map

# 球员榜维度(2026-08-16 起全字段免费投影)。原来只暴露 5 个"免费"维度
# (进球/助攻/xG/xGOT/评分),其余 fact_season_player_stats 里真实存在、
# 有真实样本量的 stat_name(黄牌/红牌/犯规/扑救/零封/拦截/抢断等)此前被
# 整体排除在 FREE_PLAYER_BOARDS 之外,属于本次要拆除的付费墙残留。
#
# 未纳入的 stat_name(有意排除,不是遗漏):
# - 4 个下划线前缀的复合/派生指标(_expected_goals_and_expected_assists_per_90 /
#   _goals_and_goal_assist / _goals_prevented / _save_percentage)——这是来源
#   数据里的内部派生字段命名惯例,语义未经核实,不作为独立榜单直接展示;
# - mins_played(出场分钟数)——不是"越高越好"的竞技排名指标,是上下文/
#   筛选字段,不适合作为榜单维度。
FREE_PLAYER_BOARDS: list[tuple[str, str]] = [
    ("goals", "进球"),
    ("goal_assist", "助攻"),
    ("expected_goals", "xG"),
    ("expected_goalsontarget", "xGOT"),
    ("rating", "评分"),
    ("expected_assists", "xA"),
    ("yellow_card", "黄牌"),
    ("red_card", "红牌"),
    ("fouls", "犯规"),
    ("total_tackle", "抢断"),
    ("interception", "拦截"),
    ("ball_recovery", "反抢回收"),
    ("effective_clearance", "解围"),
    ("outfielder_block", "封堵"),
    ("total_scoring_att", "射门"),
    ("ontarget_scoring_att", "射正"),
    ("big_chance_created", "创造绝佳机会"),
    ("big_chance_missed", "错失绝佳机会"),
    ("accurate_pass", "传球成功"),
    ("accurate_long_balls", "长传成功"),
    ("won_contest", "成功过人"),
    ("poss_won_att_3rd", "前场反抢"),
    ("defensive_contributions", "防守贡献"),
    ("penalty_won", "赢得点球"),
    ("penalty_conceded", "送点"),
    ("saves", "扑救"),
    ("clean_sheet", "零封"),
    ("goals_conceded", "失球"),
]

_BOARD_TOP_N = 10


# ── 来源方赛季球队榜(fact_season_team_stats,2026-09-10 新增)────────────
#
# 与上面的球员榜同构,但**只收 TeamSeasonStatRow 里没有同义字段的维度**——
# 那张 DTO 是我们自己从单场数据聚合出来的(silver_team_season_stats),来源
# 榜单是 FotMob 自己算的,两者口径不可能逐位相等。同一个指标在同一页出现
# 两个不同的数字比少一个榜糟糕得多,所以控球/射门/射正/xG/角球/犯规/红黄牌/
# 零封这些已经在 DTO 里的维度一律不收(来源侧对应的是
# possession_percentage_team / ontarget_scoring_att_team / expected_goals_team /
# expected_goals_conceded_team / corner_taken_team / fk_foul_lost_team /
# total_yel_card_team / total_red_card_team / clean_sheet_team)。
#
# 另外三个有意排除、不是遗漏的:
# - rating_team(FotMob 球队综合评分):来源方黑箱算法,没有可解释口径,
#   与站点"把不确定性讲清楚"的定位需要单独设计文案,不顺手塞进榜单墙;
# - home_attendance_team(上座人数):不是竞技指标,这一页是球队数据榜;
# - _xg_diff_team(xG 差):可由本页已展示的 xG 与被创造 xG 直接相减得出,
#   不占一张卡。
#
# 下划线前缀的 _set_piece_goals_* 这里**收**,与球员榜整体排除下划线指标的
# 判断不同:球员侧排除的理由是那 4 个复合/派生指标语义未经核实
# (_goals_and_goal_assist 之类),而"定位球进球/失球"语义明确、无歧义。
#
# 单位(场均 vs 赛季合计)不在这张表里写死,由 extra_json 里来源自报的
# stat_title 派生(见 _team_source_boards)——同一批榜里 poss_won_att_3rd_team
# 是场均、big_chance_team 是赛季总数,而 StatFormat 两者都可能是 'fraction',
# 靠字段名或中文标签猜必错。
FREE_TEAM_BOARDS: list[tuple[str, str]] = [
    ("poss_won_att_3rd_team", "前场反抢"),
    ("interception_team", "拦截"),
    ("total_tackle_team", "抢断"),
    ("effective_clearance_team", "解围"),
    ("accurate_pass_team", "传球成功"),
    ("accurate_long_balls_team", "长传成功"),
    ("accurate_cross_team", "传中成功"),
    ("touches_in_opp_box_team", "对方禁区触球"),
    ("big_chance_team", "创造绝佳机会"),
    ("big_chance_missed_team", "错失绝佳机会"),
    ("_set_piece_goals_team", "定位球进球"),
    ("_set_piece_goals_conceded_team", "定位球失球"),
    ("penalty_won_team", "赢得点球"),
    ("penalty_conceded_team", "送点"),
    ("saves_team", "扑救"),
    ("phys_tdc_team", "跑动距离"),
]


def _team_source_boards(
    conn: sqlite3.Connection, league_id: int, season: str | None
) -> list[dict]:
    """来源方球队榜(top 10/维度)。空维度整条不返回,不制造空卡片墙。

    season 由调用方传入**已解析好的赛季**(与本页 rows 同一个),不自己再解析
    一次——两处各自解析会让同一个页面上半部分和下半部分显示不同赛季。
    """
    if season is None:
        return []
    display = team_display_map(conn)
    boards: list[dict] = []
    for stat_name, label_zh in FREE_TEAM_BOARDS:
        try:
            rows = conn.execute(
                """SELECT Team_ID, Team_Name, rank, value, extra_json
                   FROM fact_season_team_stats
                   WHERE League_ID=? AND Season=? AND stat_name=?
                   ORDER BY rank LIMIT ?""",
                (league_id, season, stat_name, _BOARD_TOP_N),
            ).fetchall()
        except sqlite3.OperationalError:
            # 表尚未建立(未跑 0016 的库)——整块降级为空,不让联赛页 500。
            return []
        if not rows:
            continue
        try:
            meta = json.loads(rows[0]["extra_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        title = meta.get("stat_title")
        boards.append(
            {
                "stat_name": stat_name,
                "label_zh": label_zh,
                "stat_title": title if isinstance(title, str) else None,
                # "每场" 还是 "赛季合计":唯一可靠信号是来源自报的标题里有没有
                # "per match"。拿不到标题就是 None —— 宁可不标单位,不猜。
                "per_match": (
                    " per match" in title.lower() if isinstance(title, str) else None
                ),
                "stat_format": meta.get("stat_format"),
                "stat_decimals": meta.get("stat_decimals"),
                "entries": [
                    {
                        "team": _team_ref(r["Team_ID"], r["Team_Name"], display),
                        "rank": r["rank"],
                        "value": r["value"],
                    }
                    for r in rows
                ],
            }
        )
    return boards


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


# 一个赛季至少要踢到这个场次,才配当"默认展示的赛季"。
# 背景(2026-08-12 实测):J1(223)的 silver_team_season_stats 里
# Season='2026/2027' 每队 matches_played=1 —— 那是跨年赛制切换后刚开踢的
# 新赛季,而 _resolve_season 取 seasons[-1](字符串序)会选中它,导致
# "球队数据"页默认展示一张**单场样本冒充整季**的榜(场均值等于那一场的值)。
# 同一联赛的 2026 赛季有 20 场、2024/2025 各 38 场,才是有意义的默认。
MIN_MATCHES_FOR_DEFAULT_SEASON = 3


def _seasons_with_enough_sample(
    conn: sqlite3.Connection, table: str, league_id: int
) -> set[str]:
    """样本量足够、可以当默认赛季的赛季集合(不足的仍可被显式选择)。"""
    try:
        return {
            r[0]
            for r in conn.execute(
                f"""SELECT Season FROM {table} WHERE League_ID=?
                     GROUP BY Season HAVING MAX(COALESCE(matches_played, 0)) >= ?""",
                (league_id, MIN_MATCHES_FOR_DEFAULT_SEASON),
            )
        }
    except sqlite3.OperationalError:
        return set()


def _resolve_season(
    seasons: list[str],
    season: str | None,
    *,
    preferred: set[str] | None = None,
) -> str | None:
    """解析要展示的赛季。

    `preferred` 是"样本量足够"的赛季集合:缺省赛季只从其中选,避免把一个
    刚开踢 1 场的新赛季当成整季榜(见 MIN_MATCHES_FOR_DEFAULT_SEASON)。
    用户显式请求的赛季一律尊重,即使样本很小 —— 那是用户自己的选择。
    """
    if not seasons:
        return season
    if season is not None and season in seasons:
        return season
    pool = [s for s in seasons if s in preferred] if preferred else []
    return (pool or seasons)[-1]


def team_season_stats(
    conn: sqlite3.Connection, league_id: int, season: str | None = None
) -> dict:
    seasons = _seasons_of(conn, "silver_team_season_stats", league_id)
    if not seasons:
        return {
            "season": season,
            "available_seasons": [],
            "rows": [],
            "boards": _team_source_boards(conn, league_id, season),
        }
    season = _resolve_season(
        seasons,
        season,
        preferred=_seasons_with_enough_sample(
            conn, "silver_team_season_stats", league_id
        ),
    )
    display = team_display_map(conn)
    # xG 拆解(运动战/定位球/非点球)与总 xG 同源同口径。
    #
    # 被创造 xG 走 fact_league_table 的 xg 档:该表已随 standings 的 table_type=xg
    # 公开(xG 运气榜),这里只是换算成场均以便与 silver 的场均值同轴比较。
    # 实测确认两源同口径:曼城 2025/2026 silver 1.877 == 65.5.../38(逐队吻合)。
    # LEFT JOIN —— 并非每个联赛赛季都有 xg 档(如 J1 2026、瑞超 2024),
    # 缺失时 avg_expected_goals_conceded 为 None,前端据此降级,不补 0。
    #
    # 2026-08-16 起(除"每日精选"外全站比赛内容全部免费):角球/黄牌/红牌/
    # 零封/BTTS 与射门/xG 等字段同属免费投影,一并 SELECT。
    rows = conn.execute(
        """SELECT s.Team_ID, s.matches_played, s.avg_total_shots,
                  s.avg_shots_on_target, s.avg_possession, s.avg_expected_goals,
                  s.avg_expected_goals_on_target, s.avg_expected_goals_open_play,
                  s.avg_expected_goals_set_play, s.avg_expected_goals_non_penalty,
                  s.avg_corners, s.avg_fouls, s.avg_yellow_cards, s.avg_red_cards,
                  s.clean_sheets, s.btts_matches, s.btts_pct,
                  CASE WHEN COALESCE(x.played, 0) > 0
                       THEN x.xg_conceded * 1.0 / x.played END
                    AS avg_expected_goals_conceded
           FROM silver_team_season_stats s
           LEFT JOIN fact_league_table x
             ON x.League_ID = s.League_ID AND x.Season = s.Season
            AND x.Team_ID = s.Team_ID AND x.table_type = 'xg'
           WHERE s.League_ID=? AND s.Season=?
           ORDER BY s.Team_ID""",
        (league_id, season),
    ).fetchall()
    return {
        "season": season,
        "available_seasons": seasons,
        "boards": _team_source_boards(conn, league_id, season),
        "rows": [
            {
                "team": _team_ref(r["Team_ID"], None, display),
                "matches_played": r["matches_played"],
                "avg_total_shots": r["avg_total_shots"],
                "avg_shots_on_target": r["avg_shots_on_target"],
                "avg_possession": r["avg_possession"],
                "avg_expected_goals": r["avg_expected_goals"],
                "avg_expected_goals_on_target": r["avg_expected_goals_on_target"],
                "avg_expected_goals_open_play": r["avg_expected_goals_open_play"],
                "avg_expected_goals_set_play": r["avg_expected_goals_set_play"],
                "avg_expected_goals_non_penalty": r["avg_expected_goals_non_penalty"],
                "avg_expected_goals_conceded": r["avg_expected_goals_conceded"],
                "avg_corners": r["avg_corners"],
                "avg_fouls": r["avg_fouls"],
                "avg_yellow_cards": r["avg_yellow_cards"],
                "avg_red_cards": r["avg_red_cards"],
                "clean_sheets": r["clean_sheets"],
                "btts_matches": r["btts_matches"],
                "btts_pct": r["btts_pct"],
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


# ── 联赛速览(season profile):四张银层表 → 四张图 ────────────────────
#
# silver_goal_minute_buckets(312 行)/ silver_score_distribution(1,278 行)/
# silver_over_under_thresholds(252 行)/ silver_league_season_summary(42 行)
# 早就构建完成,但**前端零消费** —— legacy 付费端点 /api/league/{id}/betting 把
# 它们查出来过,而 grep 全前端没有任何消费方;/api/v1 下则完全没有对应路由。
# 宪法 §10.1 禁止继续扩展 legacy,所以这里在 v1 新建。
#
# 这四组数据是"让 30 岁用户看得懂高阶数据"里门槛最低的一档:进球时段、
# 常见比分、大小球阈值、主客胜率 —— 都是竞彩用户本来就在用的语言。

def _season_profile_seasons(conn: sqlite3.Connection, league_id: int) -> list[str]:
    return _seasons_of(conn, "silver_league_season_summary", league_id)


def league_season_profile(
    conn: sqlite3.Connection, league_id: int, season: str | None = None
) -> dict:
    """联赛赛季速览:概览 + 进球时段 + 比分分布 + 大小球阈值。

    任一子块缺数据时返回空列表,不补零、不编造 —— 调用方按空态渲染。
    """
    seasons = _season_profile_seasons(conn, league_id)
    if not seasons:
        return {
            "season": season,
            "available_seasons": [],
            "summary": None,
            "goal_minutes": [],
            "score_distribution": [],
            "over_under": [],
        }
    season = _resolve_season(seasons, season)

    def rows(sql: str) -> list:
        try:
            return conn.execute(sql, (league_id, season)).fetchall()
        except sqlite3.OperationalError:
            return []

    summary_row = rows(
        """SELECT total_matches, home_win_pct, draw_pct, away_win_pct, btts_pct,
                  clean_sheet_pct, avg_total_goals, home_away_goal_diff
             FROM silver_league_season_summary WHERE League_ID=? AND Season=?"""
    )
    goal_minutes = rows(
        """SELECT bucket, goal_count, pct FROM silver_goal_minute_buckets
            WHERE League_ID=? AND Season=? ORDER BY bucket"""
    )
    scores = rows(
        """SELECT home_score, away_score, match_count, pct
             FROM silver_score_distribution WHERE League_ID=? AND Season=?
            ORDER BY match_count DESC"""
    )
    over_under = rows(
        """SELECT threshold, over_count, under_count, over_pct, under_pct
             FROM silver_over_under_thresholds WHERE League_ID=? AND Season=?
            ORDER BY threshold"""
    )

    return {
        "season": season,
        "available_seasons": seasons,
        "summary": dict(summary_row[0]) if summary_row else None,
        "goal_minutes": [dict(r) for r in goal_minutes],
        "score_distribution": [dict(r) for r in scores],
        "over_under": [dict(r) for r in over_under],
    }
