-- 0016: fact_season_team_stats —— 来源方(FotMob)提供的赛季**球队**榜单。
--
-- 背景(2026-09-10,反编译 FotMob 官方安卓包 236.17398.20260827 +
-- 公网 API 实测):站长发现联赛球队统计里有一条「单场在进攻三区赢得的球权」,
-- 但单场比赛的技术统计里没有。查证结论:
--
-- 1. 这不是 FotMob 漏放,是数据模型决定的。他们的**单场**球队统计模型
--    com.fotmob.models.PeriodOptaStats 一共 72 个字段,里面没有任何
--    possession_won / final_third 语义的项(最接近的 FinalThirdEntries 和
--    BallRecoveries 都是别的指标)。
-- 2. 该指标只存在于**赛季聚合**模型 com.fotmob.models.Stats.
--    possessionWonFinal3rd,对外表现为联赛榜单 stat_name=
--    'poss_won_att_3rd_team'(header "Possession won final 3rd per match",
--    category "Defending", StatFormat 'fraction')。实测英超 2025/2026:
--    Brighton StatValue=5.1 rank=1,MatchesPlayed=38 —— StatValue 就是来源
--    直接给的场均值,不需要我们再除场次。
-- 3. 我们自己算不出来:fact_team_match_stats.extra_json 的 47 个 key 里
--    没有任何能推导它的字段。所以这张表不是"聚合层的第二份拷贝",而是
--    一类**只能按赛季榜采**的指标的唯一落点。
--
-- 结构与 fact_season_player_stats 完全同构(同一份联赛 API 响应的
-- stats.teams[] vs stats.players[]),按 (League_ID, Season) 先删后插;
-- extra_json 除未映射的行字段外还存该榜元数据(stat_title/stat_format/
-- stat_decimals/category)——同一批榜里既有场均值也有赛季总数,渲染单位只能
-- 靠 stat_format 判断,不能靠字段名或中文标签猜。
--
-- 与 backend/studio/team_style.py 的重叠(如实记录,本次不动):那里的
-- METRIC_REGISTRY 也解析了 poss_won_att_3rd_team(标签「前场夺回」),但整条
-- 链路无生产调用方、platform.db 的 team_style_profiles 生产 0 行。本表落地后
-- 二者语义重叠,收敛还是删除需另行决策。
--
-- 空库骨架:列定义的真源是 backend/schema.py::SEASON_TEAM_STATS_CORE_COLUMNS,
-- 与 0012 同一约定,不手抄进代码。

CREATE TABLE IF NOT EXISTS fact_season_team_stats (
    "League_ID" INTEGER,
    "Season"    TEXT,
    "stat_name" TEXT,
    "Team_ID"   INTEGER,
    "Team_Name" TEXT,
    "rank"      INTEGER,
    "value"     REAL,
    "extra_json" TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fact_season_team_stats_natural
    ON fact_season_team_stats (League_ID, Season, stat_name, Team_ID);

CREATE INDEX IF NOT EXISTS idx_fact_season_team_stats_season
    ON fact_season_team_stats (League_ID, Season, stat_name);
