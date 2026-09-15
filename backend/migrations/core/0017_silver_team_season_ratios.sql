-- 0017: silver_team_season_ratios —— 球队象限图复合(比率)指标的赛季聚合。
--
-- 背景(2026-09-14,站长要求扩展球队象限图的"A 比 B"类视角):
-- silver_team_season_stats 的 12 个 avg_* 字段各自用 AVG(CASE WHEN ... IS NOT
-- NULL ...) 算,每个字段的非空分母互不相同——"占比"如果拿两个 avg_* 相除,
-- 本质是"比值之比"而不是真正的配对比值。正确做法(backend/queries/
-- attack_chain.py::_ratio_field 的既有节奏)是分子分母取自**同一场**,
-- 逐场配对后再对窗口求和;本表把这个节奏从"窗口尺度"搬到"赛季尺度"。
--
-- 窄长表而非每个比率一列:每新增一个比率型视角只需要在
-- backend/silver/ratio_metrics.py 加一行 RatioSpec + backend/api/schemas.py
-- 加一个具名字段,不需要再做一次 migration。不存 value 列——
-- value = scale × numerator_sum / denominator_sum,scale 是显示单位的元数据,
-- 改单位不该触发 silver 重建。
--
-- 空库骨架:列定义的真源是 backend/schema.py::SILVER_TEAM_SEASON_RATIOS_COLUMNS,
-- 与 0012/0016 同一约定,不手抄进代码。

CREATE TABLE IF NOT EXISTS silver_team_season_ratios (
    "League_ID"            INTEGER,
    "Season"               TEXT,
    "Team_ID"              INTEGER,
    "metric_key"           TEXT,     -- 与 backend/metrics/registry.py 的 canonical_key 同名
    "numerator_sum"        REAL,     -- 赛季累计分子(只累加配对成功的场次)
    "denominator_sum"      REAL,     -- 赛季累计分母 —— 前端最小分母门槛看它
    "paired_matches"       INTEGER,  -- 分子分母同场均非空且分母>0 的场次数
    "matches_played"       INTEGER,  -- 该队该赛季完赛场次;与 paired_matches 不等即有缺场
    "methodology_version"  TEXT,
    "updated_at"           TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_silver_team_season_ratios_natural
    ON silver_team_season_ratios (League_ID, Season, Team_ID, metric_key);

CREATE INDEX IF NOT EXISTS idx_silver_team_season_ratios_season
    ON silver_team_season_ratios (League_ID, Season, metric_key);
