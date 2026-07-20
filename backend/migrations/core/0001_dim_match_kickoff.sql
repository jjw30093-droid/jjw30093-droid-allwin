-- dim_match 增加精确开球时刻 kickoff_at_utc(CLAUDE.md §6.2.1):
-- - 真实 allwin.db:dim_match 已存在 → CREATE IF NOT EXISTS 无效果,仅 ALTER 加列;
-- - 空库(如 --data-dir 临时目录):先按 backend/schema.py 的 DIM_MATCH_COLUMNS
--   建同构骨架,再加列,保证迁移在任何环境可重复。
-- 语义:来源只给日期时本列必须为 NULL,不得补当天 00:00 伪装精确时间。

CREATE TABLE IF NOT EXISTS dim_match (
    Match_ID INTEGER PRIMARY KEY,
    Season TEXT,
    League_ID INTEGER,
    Date TEXT,
    Home_Team_ID INTEGER,
    Away_Team_ID INTEGER,
    Home_Team_Name TEXT,
    Away_Team_Name TEXT,
    home_score INTEGER,
    away_score INTEGER,
    status TEXT,
    Referee TEXT,
    Match_Round TEXT,
    Temperature TEXT,
    Wind_Speed TEXT,
    Who_Lost_On_Penalties TEXT
);

ALTER TABLE dim_match ADD COLUMN kickoff_at_utc TEXT;

CREATE INDEX IF NOT EXISTS idx_dim_match_kickoff
    ON dim_match(kickoff_at_utc) WHERE kickoff_at_utc IS NOT NULL;
