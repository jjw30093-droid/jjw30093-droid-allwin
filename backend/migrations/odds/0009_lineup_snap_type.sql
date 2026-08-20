-- bronze_fm_lineup_snap.lineup_type 落成可索引列(PIPELINE_REDESIGN_V2 P2,2026-08-17)
--
-- 背景:lineup_type 目前只活在 payload_json 里的同名 JSON 字段
-- (2026-08-15 起 extract_lineup_snapshot 才开始写这个键,见
-- backend/providers/fotmob_snapshots.py),无法被查询/过滤,也不能在 P5 的
-- 早停逻辑里当停止条件用。真实分布(228 行全量实测,2026-08-17):payload
-- 缺 lineup_type 键=154、lastStarting11=57、predicted(source=enetpulse)=16、
-- standard(source=null)=1——四种真实值,不是文档此前假设的只有
-- lastStarting11 一种。
--
-- 回填规则(不编造):
--   - payload_json 里存在 'lineup_type' 键 → 原样落成该列的值(包含
--     'standard' 这种边缘值——它只有 1 条样本且 0 名首发,本迁移不对它做任何
--     "等同已确认官方名单"的语义升级,只是如实搬运原值);
--   - 键缺失(旧快照,本字段上线前写入)→ 保持 NULL,不猜测默认值;
--   - json_extract 对不存在的 JSON 路径本就返回 NULL,天然满足"缺失即 NULL",
--     不需要额外的 CASE 判断。
-- 幂等:仅回填 lineup_type IS NULL 的行,重跑无副作用(与
-- core/0002_kickoff_provenance.sql、platform/0003_snapshot_kickoff_provenance.sql
-- 的 ADD COLUMN + UPDATE ... WHERE col IS NULL 同一模式)。
--
-- 索引服务两个用途:按 lineup_type 过滤/统计,以及未来 P5 早停判断"这场比赛
-- 是否已经拿到过某种类型的阵容快照"(该判断需要复合 fotmob_match_id 过滤,
-- 单独给 lineup_type 建索引选择性太低,直接按早停会用到的组合键建)。

ALTER TABLE bronze_fm_lineup_snap ADD COLUMN lineup_type TEXT;

UPDATE bronze_fm_lineup_snap
SET lineup_type = json_extract(payload_json, '$.lineup_type')
WHERE lineup_type IS NULL;

CREATE INDEX idx_fm_lineup_type ON bronze_fm_lineup_snap(fotmob_match_id, lineup_type);
