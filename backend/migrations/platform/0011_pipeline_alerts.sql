-- 0011: 管道告警账本(数据管道重建 Phase 5,方糖/Server 酱)。
--
-- 设计原则(前身项目教训):
-- - 告警与失败日志/审计事件分表——前身项目把两者塞进同一张 ingest_failure_log
--   并靠 resolved=1 区分,自己也承认是设计缺陷;这里从一开始就独立建表。
-- - "持久化优先、推送其次":backend/notify 先落本表、再尝试推送;推送失败不影响
--   记录本身。notify_result 记录推送结局(sent / suppressed:* / failed:*),
--   NULL 表示已落库但推送流程尚未完成(ops_check 把 pending 超 1 小时报 WARN)。
-- - 本表必须有 migration 文件——前身项目这张关键表是在服务器上手搓的,
--   仓库里没有 CREATE TABLE,恢复演练时才发现。
--
-- dedup_key:同一 dedup_key 24 小时内已成功推送(notify_result='sent')的告警
-- 不再重复推(记 suppressed:dedup),防止 15 分钟链把同一故障推爆免费配额。
-- sendkey 等凭证绝不允许出现在任何列(backend/notify 负责脱敏,测试钉死)。

CREATE TABLE pipeline_alerts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  level         TEXT NOT NULL CHECK (level IN ('INFO','WARNING','CRITICAL')),
  source        TEXT NOT NULL,          -- pipeline_step_failure / source_waf_blocked / ...
  dedup_key     TEXT NOT NULL,
  title         TEXT NOT NULL,
  body          TEXT,
  created_at    TEXT NOT NULL,          -- UTC ISO
  notified_at   TEXT,                   -- 仅推送成功(sent)时写
  notify_result TEXT                    -- sent / suppressed:disabled|dedup|quota / failed:*
);

CREATE INDEX idx_pipeline_alerts_dedup ON pipeline_alerts(dedup_key, created_at DESC);
CREATE INDEX idx_pipeline_alerts_created ON pipeline_alerts(created_at DESC);
CREATE INDEX idx_pipeline_alerts_pending ON pipeline_alerts(created_at)
  WHERE notify_result IS NULL;
