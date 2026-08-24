"""
schema.py — 表结构的唯一定义来源(init_db.py 建表、ingest_match.py 落库都从这里取列名)。
列名严格对齐 fotmob_client.py 里 parse_* 方法返回字典的 key。
"""

# ── fact_player_match_stats: parse_player_stats_records() 返回的固定宽表列 ──
# (Match_ID, Player_ID) 是自然主键,但落库策略不依赖唯一约束(见 ingest_match.py),
# 这里只按 parse 返回顺序声明列名 + 类型亲和。
PLAYER_STATS_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("Player_ID", "TEXT"),
    ("Player_Opta_ID", "TEXT"),
    ("Team_ID", "INTEGER"),
    ("is_goalkeeper", "INTEGER"),
    ("is_captain", "INTEGER"),
    ("shirt_number", "TEXT"),
    ("position_id", "INTEGER"),
    ("usual_position", "TEXT"),
    ("rating_title", "REAL"),
    ("minutes_played", "INTEGER"),
    ("player_name", "TEXT"),
    ("goals", "REAL"),
    ("assists", "REAL"),
    ("expected_goals", "REAL"),
    ("expected_assists", "REAL"),
    ("xg_and_xa", "REAL"),
    ("expected_goals_on_target_variant", "REAL"),
    ("expected_goals_non_penalty", "REAL"),
    ("ShotsOnTarget", "REAL"),
    ("ShotsOffTarget", "REAL"),
    ("shot_accuracy", "REAL"),
    ("blocked_shots", "REAL"),
    ("big_chance_missed_title", "REAL"),
    ("shots_woodwork", "REAL"),
    ("missed_penalty", "REAL"),
    ("shotmap", "TEXT"),
    ("accurate_passes", "REAL"),
    # 2026-08-23:传球成功次数的分母(尝试传球总数)。原始 payload 该字段是
    # fractionWithPercentage 类型({"value":37,"total":40}),此前只取 value,
    # "成功传球 37 次"没有分母看不出好坏。见 _build_stat_lookup 的 __total 约定。
    ("accurate_passes_total", "REAL"),
    ("chances_created", "REAL"),
    ("big_chance_created_team_title", "REAL"),
    ("passes_into_final_third", "REAL"),
    ("accurate_crosses", "REAL"),
    ("long_balls_accurate", "REAL"),
    ("touches", "REAL"),
    ("touches_opp_box", "REAL"),
    ("dispossessed", "REAL"),
    ("dribbles_succeeded", "REAL"),
    ("matchstats.headers.tackles", "REAL"),
    ("shot_blocks", "REAL"),
    ("clearances", "REAL"),
    ("headed_clearance", "REAL"),
    ("interceptions", "REAL"),
    ("recoveries", "REAL"),
    ("dribbled_past", "REAL"),
    ("ground_duels_won", "REAL"),
    ("aerials_won", "REAL"),
    ("defensive_actions", "REAL"),
    ("last_man_tackle", "REAL"),
    ("clearance_off_the_line", "REAL"),
    ("duel_won", "REAL"),
    ("duel_lost", "REAL"),
    ("fouls", "REAL"),
    ("was_fouled", "REAL"),
    ("penalties_won", "REAL"),
    ("conceded_penalties", "REAL"),
    ("errors_led_to_goal", "REAL"),
    ("corners", "REAL"),
    ("Offsides", "REAL"),
    ("owngoal", "REAL"),
    ("fantasy_points", "REAL"),
    ("saves", "REAL"),
    ("goals_conceded", "REAL"),
    ("expected_goals_on_target_faced", "REAL"),
    ("goals_prevented", "REAL"),
    ("keeper_diving_save", "REAL"),
    ("saves_inside_box", "REAL"),
    ("keeper_sweeper", "REAL"),
    ("punches", "REAL"),
    ("player_throws", "REAL"),
    ("keeper_high_claim", "REAL"),
    ("saved_penalties", "REAL"),
    ("saved_penalties_in_shootout", "REAL"),
    ("physical_metrics_topspeed", "REAL"),
    ("physical_metrics_distance_covered", "REAL"),
    ("physical_metrics_walking", "REAL"),
    ("physical_metrics_jogging", "REAL"),
    ("physical_metrics_running", "REAL"),
    ("physical_metrics_sprinting", "REAL"),
    ("physical_metrics_number_of_sprints", "REAL"),
]

# ── fact_team_match_stats: parse_team_stats_records() 的 key 因比赛/联赛而异 ──
# 方案:核心列(稳定存在) + extra_json 兜底(动态 key 原样存 JSON)。
# 原因:
#   - EAV(长表)虽然完全灵活,但每次查询都要 pivot,简单的"某场比赛控球率"都要写子查询。
#   - 纯固定宽表需要提前知道所有可能出现的统计项,而 FotMob 不同联赛/赛事可用的统计
#     类别不同,新增字段会频繁触发 ALTER TABLE,且历史缺失字段会有大量 NULL 列。
#   - 核心列(Match_ID/Team_ID/Period/Goals)覆盖率 100%、査询/JOIN 高频,值得做成真列;
#     其余动态字段整体存进 extra_json(TEXT,JSON 编码),不丢数据,需要时用 SQLite 的
#     json_extract(extra_json, '$.key') 取值,以后发现某个字段稳定出现且高频查询,
#     再单独提升为真列。
#
# 已确认的数据源限制(2020/21~2025/26 全量核对过,非解析 bug):
#   老赛季 content.stats.Periods 里只有 "All"，没有 "FirstHalf"/"SecondHalf"——
#   FotMob 从大约 2022/23 赛季中段才开始提供半场拆分统计。受影响场次(每队仅 1 行
#   即 Period='All'，而非完整的 3 个 Period × 2 队 = 6 行):
#   2020/2021 373/380、2021/2022 377/380、2022/2023 214/380；2023/2024 及以后
#   全部有完整半场拆分。查询半场数据前先确认赛季/场次是否落在此范围内。
TEAM_STATS_CORE_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("Team_ID", "INTEGER"),
    ("Period", "TEXT"),
    ("Goals", "REAL"),
]

DIM_MATCH_COLUMNS = [
    ("Match_ID", "INTEGER PRIMARY KEY"),
    ("Season", "TEXT"),
    ("League_ID", "INTEGER"),
    ("Date", "TEXT"),
    ("Home_Team_ID", "INTEGER"),
    ("Away_Team_ID", "INTEGER"),
    ("Home_Team_Name", "TEXT"),
    ("Away_Team_Name", "TEXT"),
    ("home_score", "INTEGER"),
    ("away_score", "INTEGER"),
    ("status", "TEXT"),
    ("Referee", "TEXT"),
    ("Match_Round", "TEXT"),
    ("Temperature", "TEXT"),
    ("Wind_Speed", "TEXT"),
    ("Who_Lost_On_Penalties", "TEXT"),
    # 精确 UTC 开球时刻(CLAUDE.md §6.2.1);来源只给日期时为 NULL,不伪装精确时间。
    # 已有库经 backend/migrations/core/0001_dim_match_kickoff.sql 补列。
    ("kickoff_at_utc", "TEXT"),
    # 开球时间精度与来源(provenance,§6.2.1);migrations/core/0002 补列。
    # kickoff_precision ∈ exact / date_only / unknown;kickoff_source 可空,不编造。
    ("kickoff_precision", "TEXT"),
    ("kickoff_source", "TEXT"),
    # 场馆与天气描述(比赛详情"比赛信息"卡片);migrations/core/0004 补列。
    # 历史比赛无原始快照可回填,恒为 NULL,不编造。
    ("Venue_Name", "TEXT"),
    ("Venue_City", "TEXT"),
    ("Venue_Country", "TEXT"),
    ("Weather_Description", "TEXT"),
    # 主客配对配色(2026-08-24,复用 FotMob 已做撞色规避的逐场配色,不是
    # 球队固定色);migrations/core/0008 补列。历史比赛无原始快照可回填,
    # 恒为 NULL,不编造。
    ("Home_Team_Color_Light", "TEXT"),
    ("Home_Team_Color_Dark", "TEXT"),
    ("Away_Team_Color_Light", "TEXT"),
    ("Away_Team_Color_Dark", "TEXT"),
]

DIM_PLAYER_COLUMNS = [
    ("Player_ID", "TEXT PRIMARY KEY"),
    ("Player_Name", "TEXT"),
]

SHOTMAP_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("Player_ID", "TEXT"),
    ("Team_ID", "INTEGER"),
    ("Minute", "INTEGER"),
    ("Period", "TEXT"),
    ("X_Coord", "REAL"),
    ("Y_Coord", "REAL"),
    ("xG", "REAL"),
    ("xGOT", "REAL"),
    ("Situation", "TEXT"),
    ("Outcome", "TEXT"),
    ("Shot_Type", "TEXT"),
    # 2026-08-23 见 backend/migrations/core/0005_shotmap_raw_fields.sql——
    # 原始 payload 早就带这些字段,此前解析器没取。旧场次(未重新抓取)
    # 这些列恒为 NULL,不是"这场比赛没有这个数据"。
    ("Shot_ID", "INTEGER"),
    ("Is_Blocked", "INTEGER"),
    ("Is_On_Target", "INTEGER"),
    ("Is_From_Inside_Box", "INTEGER"),
    ("Minute_Added", "INTEGER"),
    ("Keeper_ID", "TEXT"),
]


def _quote(col: str) -> str:
    """带点号的列名(如 matchstats.headers.tackles)需要用双引号包裹。"""
    return f'"{col}"'


# ── fact_league_table: parse_league_table() 返回，联赛积分榜(all/home/away/form/xg 五档) ──
# 自然键 (League_ID, Season, table_type, Team_ID)，不强制唯一约束，落库前按
# (League_ID, Season) 先删后插。核心列覆盖 all/home/away/form 四档共有字段；
# xg 档独有的 xg/xg_conceded/x_points/x_position 在其它档为 NULL；
# 其余字段(goalsScored 冗余、xgDiff 等派生量、pageUrl 等)进 extra_json。
LEAGUE_TABLE_CORE_COLUMNS = [
    ("League_ID", "INTEGER"),
    ("Season", "TEXT"),
    ("table_type", "TEXT"),
    ("Team_ID", "INTEGER"),
    ("Team_Name", "TEXT"),
    ("position", "INTEGER"),
    ("played", "INTEGER"),
    ("wins", "INTEGER"),
    ("draws", "INTEGER"),
    ("losses", "INTEGER"),
    ("goals_for", "INTEGER"),
    ("goals_against", "INTEGER"),
    ("goal_diff", "INTEGER"),
    ("points", "INTEGER"),
    ("deduction", "INTEGER"),
    ("qual_color", "TEXT"),
    ("xg", "REAL"),
    ("xg_conceded", "REAL"),
    ("x_points", "REAL"),
    ("x_position", "INTEGER"),
]

# ── fact_match_events: parse_match_events() 返回，比赛事件时间线(按数组顺序一行一事件) ──
# 无稳定行 id，按 Match_ID 先删后插。event_type ∈ {Goal, Card, Substitution, AddedTime, Half}，
# 不同类型只填各自相关字段，其余为 NULL；shotmapEvent 等嵌套细节进 extra_json。
MATCH_EVENTS_CORE_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("event_index", "INTEGER"),
    ("event_type", "TEXT"),
    ("minute", "INTEGER"),
    ("overload_time", "INTEGER"),
    ("is_home", "INTEGER"),
    ("home_score", "INTEGER"),
    ("away_score", "INTEGER"),
    ("player_id", "TEXT"),
    ("player_name", "TEXT"),
    ("card_type", "TEXT"),
    ("assist_player_id", "TEXT"),
    ("assist_player_name", "TEXT"),
    ("sub_in_player_id", "TEXT"),
    ("sub_in_player_name", "TEXT"),
    ("sub_out_player_id", "TEXT"),
    ("sub_out_player_name", "TEXT"),
    ("minutes_added", "INTEGER"),
    ("event_id", "TEXT"),
]

# ── fact_match_lineup: parse_lineup_records() 返回，阵容与阵型(每场每队每人一行) ──
# 只含出场名单(首发 starters + 替补 subs)，不含教练/伤停名单。
# 自然键 (Match_ID, Player_ID)，按 Match_ID 先删后插。
MATCH_LINEUP_CORE_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("Team_ID", "INTEGER"),
    ("is_home", "INTEGER"),
    ("formation", "TEXT"),
    ("Player_ID", "TEXT"),
    ("player_name", "TEXT"),
    ("shirt_number", "TEXT"),
    ("position_id", "INTEGER"),
    ("usual_position_id", "INTEGER"),
    ("is_starter", "INTEGER"),
    ("is_captain", "INTEGER"),
    ("country_code", "TEXT"),
    ("market_value", "INTEGER"),
    ("rating", "REAL"),
    ("sub_in_time", "INTEGER"),
    ("sub_out_time", "INTEGER"),
]

# ── fact_match_momentum: parse_momentum_records() 返回，逐分钟"势头"曲线 ──
# 2026-08-23 对照 FotMob 官方安卓包核实,payload 里一直有 content.momentum.
# main.data(约 90-95 个 {minute, value} 点),此前从未解析。value 是 FotMob
# 自己算的黑箱综合评分(不是本站可复现的口径——不同于 fact_shotmap 能自己
# 按 xG 重新聚合,这个数字来源方法论未公开),真实一场比赛(5107575)实测
# 用进球事件反向验证过正负号含义:正值=主队占优,负值=客队占优
# (63' 主队进球前后动量 27→65,90' 客队进球后动量转负)。
# 无稳定行 id,按 Match_ID 先删后插,与 fact_shotmap 同一策略。
MOMENTUM_COLUMNS = [
    ("Match_ID", "INTEGER"),
    ("Minute", "REAL"),
    ("Value", "REAL"),
]

# ── fact_season_player_stats: parse_season_player_stats() 返回，赛季球员榜单 ──
# 每个统计维度(stat_name，如 goals/assists/rating 等)一份全量排名，来自联赛 API
# stats.players[].fetchAllUrl 拉取的完整 JSON(不止 topThree)。
# 自然键 (League_ID, Season, stat_name, Player_ID)，按 (League_ID, Season) 先删后插。
SEASON_PLAYER_STATS_CORE_COLUMNS = [
    ("League_ID", "INTEGER"),
    ("Season", "TEXT"),
    ("stat_name", "TEXT"),
    ("Player_ID", "TEXT"),
    ("Player_Name", "TEXT"),
    ("Team_ID", "INTEGER"),
    ("Team_Name", "TEXT"),
    ("rank", "INTEGER"),
    ("value", "REAL"),
]

# ── dim_team_i18n / dim_player_i18n: 中文映射维度(CLAUDE.md §2) ──
# 独立于 Bronze 原表，不改 dim_match/dim_player 的英文列。i18n 是数据层的
# 派生维度，一旦建好，Silver 聚合和前端展示按 Team_ID/Player_ID 关联即可
# 自动带中文，不依赖上层，也不需要 Silver/前端跟着改。
#
# 球员译名策略(生成脚本见 backend/i18n/):
#   - 东亚球员(国籍 CHN/JPN/KOR/PRK):name_zh 是真实汉字/对应全名，
#     name_zh_short 与 name_zh 相同——不做姓氏简称(“孙兴慜”不能简写成“孙”，
#     这是东亚人名结构和西方人名结构的本质区别，不是同一套规则)。
#   - 其余球员:Qwen-MT 翻译得到带“·”分隔的全名音译存进 name_zh；
#     name_zh_short 取“·”后最后一段，即中文体育媒体日常只用姓氏的简称
#     (如“马库斯·拉什福德”→“拉什福德”)。
#   - source 记录来源(manual_seed / qwen-mt-plus)，confidence 仅 LLM 翻译
#     有意义(0~1)，manual_seed 固定 1.0；needs_review 标记待人工复核的条目。
DIM_TEAM_I18N_COLUMNS = [
    ("Team_ID", "INTEGER PRIMARY KEY"),
    ("name_en", "TEXT"),
    ("name_zh", "TEXT"),
    ("source", "TEXT"),
    ("updated_at", "TEXT"),
]

DIM_PLAYER_I18N_COLUMNS = [
    ("Player_ID", "TEXT PRIMARY KEY"),
    ("name_en", "TEXT"),
    ("name_zh", "TEXT"),
    ("name_zh_short", "TEXT"),
    ("source", "TEXT"),
    ("model", "TEXT"),
    ("confidence", "REAL"),
    ("needs_review", "INTEGER"),
    ("updated_at", "TEXT"),
]


# ── Silver 聚合层(ROADMAP.md Phase 1.2)──────────────────────────────
# 只从 Bronze(dim_match / fact_team_match_stats / fact_match_events)算出来，
# 幂等落库，不是"实时 SQL 视图"；也不重复 FotMob 自己已经预聚合好的
# fact_league_table / fact_season_player_stats。只存 ID + 数字，不存中文名——
# 中文名在 Serving 层按 Team_ID/Player_ID 关联 dim_team_i18n/dim_player_i18n。
# 聚合脚本:backend/silver/build_silver.py。
#
# 口径说明(和字段体检结论对齐，见 build_silver.py 顶部注释)：
#   - 只聚合 dim_match.status = 'Finish' 的场次(目前 2280/2280 全部满足，
#     但代码仍要显式过滤，防止未来数据出现未完赛/取消的场次)。
#   - 大小球/比分分布/进球时间分布全部由 dim_match 的最终比分/由
#     fact_match_events 的 event_type='Goal' 算，不依赖 extra_json。
#   - touches_opp_box(禁区触球次数)只有 2024/2025、2025/2026 两个赛季
#     覆盖率 100%，更早的赛季覆盖率 73%~87%，是 FotMob 历史采集限制
#     （随赛季分布，不是随机缺失，也不是本项目的 bug）——所以本项目只在
#     这两个赛季计算 avg_touches_opp_box，其余赛季该列显式存 NULL，
#     不做插补/不用低覆盖率赛季的数字滥竽充数。

SILVER_TEAM_SEASON_STATS_COLUMNS = [
    ("League_ID", "INTEGER"),
    ("Season", "TEXT"),
    ("Team_ID", "INTEGER"),
    ("matches_played", "INTEGER"),
    ("avg_total_shots", "REAL"),
    ("avg_shots_on_target", "REAL"),
    ("avg_possession", "REAL"),
    ("avg_corners", "REAL"),
    ("avg_fouls", "REAL"),
    ("avg_yellow_cards", "REAL"),
    ("avg_red_cards", "REAL"),
    ("avg_expected_goals", "REAL"),
    ("avg_expected_goals_non_penalty", "REAL"),
    ("avg_expected_goals_open_play", "REAL"),
    ("avg_expected_goals_set_play", "REAL"),
    ("avg_expected_goals_on_target", "REAL"),
    # touches_opp_box 仅 2024/2025、2025/2026 有数据，其余赛季固定 NULL
    # （FotMob 历史采集限制，非 bug，见本文件顶部口径说明）。
    ("avg_touches_opp_box", "REAL"),
    ("clean_sheets", "INTEGER"),
    ("btts_matches", "INTEGER"),
    ("btts_pct", "REAL"),
    ("updated_at", "TEXT"),
]

SILVER_LEAGUE_SEASON_SUMMARY_COLUMNS = [
    ("League_ID", "INTEGER"),
    ("Season", "TEXT"),
    ("total_matches", "INTEGER"),
    ("home_win_pct", "REAL"),
    ("draw_pct", "REAL"),
    ("away_win_pct", "REAL"),
    ("btts_pct", "REAL"),
    ("clean_sheet_pct", "REAL"),
    ("avg_total_goals", "REAL"),
    # 主场场均进球 - 客场场均进球，纯粹由 dim_match 比分算，不依赖任何模型。
    ("home_away_goal_diff", "REAL"),
    ("updated_at", "TEXT"),
]

# 长表格式：每个(League_ID, Season, threshold)一行，threshold 固定取
# [0.5, 1.5, 2.5, 3.5, 4.5, 5.5] 六档，覆盖常见大小球盘口线。
SILVER_OVER_UNDER_THRESHOLDS_COLUMNS = [
    ("League_ID", "INTEGER"),
    ("Season", "TEXT"),
    ("threshold", "REAL"),
    ("over_count", "INTEGER"),
    ("under_count", "INTEGER"),
    ("over_pct", "REAL"),
    ("under_pct", "REAL"),
    ("updated_at", "TEXT"),
]

# 保留方向(主客场不对称)：1-2 和 2-1 是两行不同的记录。
SILVER_SCORE_DISTRIBUTION_COLUMNS = [
    ("League_ID", "INTEGER"),
    ("Season", "TEXT"),
    ("home_score", "INTEGER"),
    ("away_score", "INTEGER"),
    ("match_count", "INTEGER"),
    ("pct", "REAL"),
    ("updated_at", "TEXT"),
]

# bucket 直接按 fact_match_events.minute 原始值分桶(0-15/16-30/31-45/
# 46-60/61-75/76-90)，不用加 overload_time——字段体检已确认补时进球的
# minute 恒为 45 或 90，overload_time 只在这两个 minute 上出现，原始分桶
# 已经能把补时进球算进正确的那一段，不需要额外处理。
SILVER_GOAL_MINUTE_BUCKETS_COLUMNS = [
    ("League_ID", "INTEGER"),
    ("Season", "TEXT"),
    ("bucket", "TEXT"),
    ("goal_count", "INTEGER"),
    ("pct", "REAL"),
    ("updated_at", "TEXT"),
]


# ── int_match_features: 逐场赛前特征表(ROADMAP.md Phase 1.M.1)────────
# 供 Gold 层模型训练用(CLAUDE.md §8),不是 Silver(不做展示，只服务建模)。
# 粒度：一行 = 一场比赛。主客两队各自的 rolling 近况在该场比赛"开赛前"就已
# 确定，不含当场数据——这是防泄露的核心约束，实现方式是按 team 分组、按
# 时间正序 shift(1) 之后再取滚动窗口，取到"上一场"为止，见
# backend/models/features/build_match_features.py 的 _rolling_features()。
#
# 字段命名规则：
#   {home|away}_{stat}_l{5|10}            —— 该队(不分主客场)近 N 场整体近况
#   {home|away}_{stat}_{home|away}_l{5|10} —— 主队"主场"近况 / 客队"客场"近况
#     (只算这个方向，没有"主队的客场近况"这类不常用的组合，够用即止)
#   {home|away}_n_matches_l{5|10}(以及 venue 版本) —— 该 rolling 实际用了
#     几场(赛季初不足 N 场时会 < N，供后续判断这行特征的可信度)
#   {stat}_diff_l{5|10} —— 主队减客队的整体近况差值(只做 xg_for / goals_for)
#
# rolling 允许跨赛季取最近 N 场(不按赛季边界截断)——这是本表的一个明确
# 简化选择，不是遗漏：跨赛季会让赛季第 1-2 轮的 rolling 用上一赛季末的比赛，
# 而不是从 0 场/1 场算起。*_n_matches_* 字段就是留给下游判断"这行数据够不
# 够可信"的信号，不需要额外的跨赛季标记列。
#
# sample_weight = exp(-0.0015 * days_from_latest)，latest 是整张表(不分
# 联赛/赛季)里最新一场比赛的日期。
FEATURE_ROLLING_STATS = [
    "xg_for",
    "xg_against",
    "goals_for",
    "goals_against",
    "shots_for",
    "shots_on_target_for",
    "possession",
]
FEATURE_WINDOWS = [5, 10]

INT_MATCH_FEATURES_COLUMNS = [
    ("match_id", "INTEGER PRIMARY KEY"),
    ("league_id", "INTEGER"),
    ("season", "TEXT"),
    ("match_date", "TEXT"),
    ("home_team_id", "INTEGER"),
    ("away_team_id", "INTEGER"),
]
for _side in ("home", "away"):
    for _stat in FEATURE_ROLLING_STATS:
        for _w in FEATURE_WINDOWS:
            INT_MATCH_FEATURES_COLUMNS.append((f"{_side}_{_stat}_l{_w}", "REAL"))
    for _w in FEATURE_WINDOWS:
        INT_MATCH_FEATURES_COLUMNS.append((f"{_side}_n_matches_l{_w}", "INTEGER"))
for _side, _venue in (("home", "home"), ("away", "away")):
    for _stat in FEATURE_ROLLING_STATS:
        for _w in FEATURE_WINDOWS:
            INT_MATCH_FEATURES_COLUMNS.append((f"{_side}_{_stat}_{_venue}_l{_w}", "REAL"))
    for _w in FEATURE_WINDOWS:
        INT_MATCH_FEATURES_COLUMNS.append((f"{_side}_n_matches_{_venue}_l{_w}", "INTEGER"))
for _stat in ("xg_for", "goals_for"):
    for _w in FEATURE_WINDOWS:
        INT_MATCH_FEATURES_COLUMNS.append((f"{_stat}_diff_l{_w}", "REAL"))
INT_MATCH_FEATURES_COLUMNS += [
    ("target_home_goals", "INTEGER"),
    ("target_away_goals", "INTEGER"),
    ("sample_weight", "REAL"),
    ("updated_at", "TEXT"),
]
del _side, _venue, _stat, _w


# ── gold_wdl_predictions: WDL 概率卡(ROADMAP.md Phase 1.M.2,路线 1 baseline)──
# Gold 层(CLAUDE.md §2),只存 test 段(walk-forward 的最近一个赛季)的预测结果，
# 供 serving 层将来读。λ 由 rolling xG 估(进攻×防守×联赛基准的经典拆解)，
# P(H/D/A) 由 Dixon-Coles 修正后的独立 Poisson 比分矩阵求得，再经 isotonic
# 校准。calibrated 恒为 1(这张表只存校准后的最终概率，不存校准前的中间值，
# 校准前后的对比数字是评估阶段的产物，不落表)。
GOLD_WDL_PREDICTIONS_COLUMNS = [
    ("match_id", "INTEGER PRIMARY KEY"),
    ("league_id", "INTEGER"),
    ("season", "TEXT"),
    ("lambda_home", "REAL"),
    ("lambda_away", "REAL"),
    ("lambda_home_is_fallback", "INTEGER"),
    ("lambda_away_is_fallback", "INTEGER"),
    ("p_home", "REAL"),
    ("p_draw", "REAL"),
    ("p_away", "REAL"),
    ("calibrated", "INTEGER"),
    # confidence/reason(ROADMAP.md Phase C2 新增)：给未来赛程的预测标注数据
    # 可信度——升班马/新球队在 bronze 无历史(rolling 全 NULL，走 λ=μ 兜底)标
    # 'low'/'promoted_no_history'；rolling 样本不足 5 场标 'low'/
    # 'insufficient_history'；其余 'normal'/NULL。历史(test 段)行这两列固定
    # 为 NULL,只有 C2 写入的未来赛程行会填。
    ("confidence", "TEXT"),
    ("reason", "TEXT"),
    ("updated_at", "TEXT"),
]
