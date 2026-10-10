-- 模型预测登记簿已下线。按外键从子表到父表删除整套遗留结构。
DROP TABLE IF EXISTS prediction_snapshot_edits;
DROP TABLE IF EXISTS prediction_evaluations;
DROP TABLE IF EXISTS prediction_outcomes;
DROP TABLE IF EXISTS prediction_manifests;
DROP TABLE IF EXISTS prediction_snapshots;
DROP TABLE IF EXISTS prediction_runs;
DROP TABLE IF EXISTS model_versions;
DELETE FROM plan_entitlements WHERE entitlement LIKE 'prediction:%';
