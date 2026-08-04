-- 模型适用联赛范围保护(CLAUDE.md §9;瑞典超接入前 model_versions 完全没有范围
-- 声明列,任何模型理论上可以被 publish/lock 到任意联赛)。
--
-- 两个新列:
--   model_versions.applicable_league_ids  — JSON 数组(如 '[47]'),模型声明自己
--     已验证适用的联赛;NULL/空 = 未声明范围。
--   prediction_snapshots.league_id        — 快照 register 时冻结(与 kickoff_precision
--     同级 provenance 字段),publish/lock 校验时直接读快照本行,不需要跨库查询 core。
--
-- 校验语义(backend/commands/predictions.py 实现,不在本迁移内):只有快照显式带了
-- league_id 才触发校验(历史/测试调用方不传 league_id 时行为完全不变,不因这次
-- 迁移导致既有 publish/lock 回归)——一旦提供 league_id,模型必须在
-- applicable_league_ids 显式包含它,否则拒绝(含模型从未声明范围的情况,不隐式放行)。
--
-- 保守回填(不影响任何既有 official/locked 正式样本,当前正式集合仍为 0):
--   - 唯一既有模型 dc-baseline-1.M.2 的训练范围文档化为 EPL(dc-baseline-1.M.2 的
--     train_range='2020/21–2024/25(EPL)',docs/model-api-contract.md §1)→ 回填
--     applicable_league_ids='[47]',保证现有 EPL publish/lock 流程零回归;
--   - 该模型若尚未创建(全新库,CLI 还没跑过 import_gold_predictions)则本 UPDATE
--     天然是 no-op——backend/cli/import_gold_predictions.py 改为在
--     get_or_create_model_version 时显式传 applicable_league_ids=[47],新库同样正确;
--   - 幂等:只在 applicable_league_ids IS NULL 时回填,重跑无副作用。
-- prediction_snapshots.league_id 对既有行留 NULL(无法逆向可靠推断,且 NULL 语义
-- 本就是"跳过校验",不影响任何既有快照的可见性或状态机)。

ALTER TABLE model_versions ADD COLUMN applicable_league_ids TEXT;
ALTER TABLE prediction_snapshots ADD COLUMN league_id INTEGER;

UPDATE model_versions
SET applicable_league_ids = '[47]'
WHERE id = 'dc-baseline-1.M.2' AND applicable_league_ids IS NULL;

-- league_id 与 kickoff_precision/kickoff_source 同级:register 时冻结的 provenance,
-- 锁定后不可改写(与 0003 的 trg_pred_snap_locked_provenance_immutable 同一兜底层级)。
CREATE TRIGGER trg_pred_snap_locked_league_immutable
BEFORE UPDATE OF league_id
ON prediction_snapshots
WHEN OLD.locked_at IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'locked prediction league_id is immutable');
END;
