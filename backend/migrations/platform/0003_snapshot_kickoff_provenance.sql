-- 预测快照冻结赛前判断所用的开球精度与来源(CLAUDE.md §6.2.1 / §9.1;P0-A)
--
-- prediction_snapshots 是不可覆盖账本。正式快照不能只保存 kickoff_at_utc,还必须
-- 冻结作出"赛前资格判断"时所用的开球精度(kickoff_precision)与来源(kickoff_source),
-- 使 publish/lock 的资格可事后审计,且不可再依赖字符串形状伪装精确。
--
-- 保守回填:全部既有快照 → 'unknown'(精度不可证明 → 不可 publish/lock)。
-- 既有 760 行均为 draft / legacy_unverified、is_official=0、未锁定,回填不影响任何
-- official/locked 正式样本(当前正式集合为 0)。source 留 NULL,由 repair 工具按可证明
-- 来源重分类(见 backend/cli/repair_kickoff_provenance.py),不在迁移里猜测。
-- 幂等:仅回填 IS NULL 的行。

ALTER TABLE prediction_snapshots ADD COLUMN kickoff_precision TEXT;
ALTER TABLE prediction_snapshots ADD COLUMN kickoff_source TEXT;

UPDATE prediction_snapshots
SET kickoff_precision = 'unknown'
WHERE kickoff_precision IS NULL;

-- 锁定后 provenance 不可改(与 0001 的实质字段不可变触发器同级兜底):
-- kickoff_at_utc 已被 0001 触发器保护,这里补 kickoff_precision / kickoff_source。
CREATE TRIGGER trg_pred_snap_locked_provenance_immutable
BEFORE UPDATE OF kickoff_precision, kickoff_source
ON prediction_snapshots
WHEN OLD.locked_at IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'locked prediction kickoff provenance is immutable');
END;
