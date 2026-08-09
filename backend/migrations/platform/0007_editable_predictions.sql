-- 正式预测账本改为"可编辑,但强制留痕"(用户明确要求,取代此前的锁定后不可改)。
--
-- 移除三个曾经拦截 UPDATE 的触发器(0001/0003/0005),同一批锁定行的字段现在
-- 可以被直接 UPDATE——这意味着"绕过 service 层也拦得住"的第二道防线不再存在,
-- 应用层的 backend/commands/predictions.py::edit_snapshot 是唯一负责写
-- prediction_snapshot_edits 留痕的地方,不能再依赖 DB 触发器兜底。
--
-- trg_pred_snap_no_delete 保留不动:物理删除正式预测依然禁止,本次改动只涉及
-- "内容可编辑",不涉及"可删除"或"可从公开列表选择性隐藏"。
DROP TRIGGER trg_pred_snap_locked_immutable;
DROP TRIGGER trg_pred_snap_locked_provenance_immutable;
DROP TRIGGER trg_pred_snap_locked_league_immutable;

ALTER TABLE prediction_snapshots ADD COLUMN last_edited_at TEXT;
ALTER TABLE prediction_snapshots ADD COLUMN edit_count INTEGER NOT NULL DEFAULT 0;

-- 防篡改的修正记录:谁在何时把哪些字段从什么值改成什么值、原因是什么。
-- audit_logs 是跨领域通用日志、没有 append-only 保护,不能单独作为这里的
-- 防篡改来源,所以单独建表并自带 no-update/no-delete 触发器(仿
-- platform/0006_team_style_profiles.sql 的写法)。
CREATE TABLE prediction_snapshot_edits (
  id            TEXT PRIMARY KEY,
  snapshot_id   TEXT NOT NULL REFERENCES prediction_snapshots(id),
  actor_user_id TEXT REFERENCES users(id),
  reason        TEXT NOT NULL,
  before_json   TEXT NOT NULL,   -- 编辑前被改字段的值(只含本次真正变化的字段,不是整行)
  after_json    TEXT NOT NULL,
  edited_at     TEXT NOT NULL
);

CREATE INDEX idx_pred_snap_edits_snapshot ON prediction_snapshot_edits(snapshot_id, edited_at);

CREATE TRIGGER trg_pred_snap_edits_no_update
BEFORE UPDATE ON prediction_snapshot_edits
BEGIN
  SELECT RAISE(ABORT, 'prediction snapshot edit history is append-only');
END;

CREATE TRIGGER trg_pred_snap_edits_no_delete
BEFORE DELETE ON prediction_snapshot_edits
BEGIN
  SELECT RAISE(ABORT, 'prediction snapshot edit history is append-only');
END;
