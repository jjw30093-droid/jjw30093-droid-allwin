-- 精确开球时间来源(provenance)收口(CLAUDE.md §6.2.1;P0-A)
--
-- 在 kickoff_at_utc 之外补充精度与来源两个维度,使"只知道自然日"与"知道精确开球
-- 时刻"从数据模型上不可混淆,杜绝把日期补成午夜后当精确时间使用:
--   kickoff_precision ∈ exact / date_only / unknown
--   kickoff_source    来源标识(FotMob 抓取链路等),可空;不得编造
--
-- 保守回填(旧数据不得默认 exact):
--   - 已有精确 kickoff_at_utc 但无法证明来源 → unknown(不冒充 exact);
--   - 只有 Date → date_only;
--   - 连 Date 都没有 → unknown。
-- kickoff_source 一律留 NULL,不回填虚构来源。
-- 幂等:仅在列尚未回填(IS NULL)时 UPDATE,重跑无副作用。

ALTER TABLE dim_match ADD COLUMN kickoff_precision TEXT;
ALTER TABLE dim_match ADD COLUMN kickoff_source TEXT;

UPDATE dim_match
SET kickoff_precision = CASE
    WHEN kickoff_at_utc IS NOT NULL THEN 'unknown'
    WHEN Date IS NOT NULL THEN 'date_only'
    ELSE 'unknown'
END
WHERE kickoff_precision IS NULL;
