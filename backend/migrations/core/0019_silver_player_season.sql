-- 0019: silver_player_season + silver_player_season_ratios —— 联赛球员象限图
-- (2026-09-15,站长要求扩展球队象限图到球员维度:xA、真实对抗成功率、三区
-- 传球这些字段球队级根本不下发,球员级全都有)。
--
-- 两张表,不合成一张(与 0017 球队侧同一个理由:一张是每球员一行的身份/
-- 门槛维度,一张是每球员每指标一行的比率长表):
--
-- silver_player_season:位置筛选与出场门槛只扫这张。team_minutes 是"该球员
-- 出场过的每支球队,该队该赛季已完赛场次 × 90"取 MAX——转会球员拿更严的
-- 分母(宁可少放人上图,也不造出"半个赛季踢满就达标"的假达标),
-- teams_count > 1 时前端在详情面板标注这个口径。Team_ID 是球员出场分钟最多
-- 的那支队(用于显示队徽/队色),与算分母用的 team_minutes 不是同一个概念。
--
-- silver_player_season_ratios:窄长表,与 0017 同构。不存 value 列——
-- value = display_scale × numerator_sum / denominator_sum,display_scale 是
-- backend/metrics/registry.py 里该指标的显示单位元数据。
--
-- 空库骨架的列定义真源是 backend/schema.py::SILVER_PLAYER_SEASON_COLUMNS /
-- SILVER_PLAYER_SEASON_RATIOS_COLUMNS,与既有惯例一致,不手抄进代码。

CREATE TABLE IF NOT EXISTS silver_player_season (
    "League_ID"        INTEGER,
    "Season"           TEXT,
    "Player_ID"        TEXT,
    "Team_ID"          INTEGER,   -- 出场分钟最多的那支队(显示用)
    "player_name"      TEXT,
    "usual_position"   INTEGER,   -- 0=门将 1=后卫 2=中场 3=前锋(实测口径)
    "appearances"      INTEGER,
    "minutes_played"   INTEGER,
    "team_minutes"      INTEGER,   -- 分母:MAX(该队该赛季完赛场次×90) over 出场过的每支队
    "minutes_share"    REAL,      -- minutes_played / team_minutes
    "teams_count"      INTEGER,   -- 该赛季效力过几支队(>1 = 转会球员)
    "updated_at"       TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_silver_player_season_natural
    ON silver_player_season (League_ID, Season, Player_ID);

CREATE INDEX IF NOT EXISTS idx_silver_player_season_season
    ON silver_player_season (League_ID, Season);

CREATE TABLE IF NOT EXISTS silver_player_season_ratios (
    "League_ID"            INTEGER,
    "Season"               TEXT,
    "Player_ID"            TEXT,
    "metric_key"           TEXT,     -- 与 backend/metrics/registry.py 的 canonical_key 同名
    "numerator_sum"        REAL,
    "denominator_sum"      REAL,     -- per-90 指标存 SUM(minutes_played);比率指标存另一统计量之和
    "paired_matches"       INTEGER,
    "methodology_version"  TEXT,
    "updated_at"           TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_silver_player_season_ratios_natural
    ON silver_player_season_ratios (League_ID, Season, Player_ID, metric_key);

CREATE INDEX IF NOT EXISTS idx_silver_player_season_ratios_season
    ON silver_player_season_ratios (League_ID, Season, metric_key);
