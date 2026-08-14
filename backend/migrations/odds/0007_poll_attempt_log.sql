-- 0007: 轮询尝试日志(数据管道重建 质量门配套)。
--
-- poll_state 只保留每个 (source, subject) 的**最后一次**轮询时间,而落库端
-- hash-diff 意味着"没有新快照" ≠ "没有轮询"——所以"T-1h 内确实每 10 分钟
-- 跑过五段节流"这件事,没有本表就无法证明。append-only,由
-- backend/ingest/poll_windows.mark_polled 顺带写入(每次真实采集尝试一行,
-- 成功与否都记,ok=0 表示该轮采集失败)。
--
-- 体量:约 700 行/天;保留 30 天,过期行由 daily_digest 任务清理
-- (不放在 mark_polled 里逐次 DELETE,避免采集热路径背上清理成本)。

CREATE TABLE poll_attempt_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  source       TEXT NOT NULL,           -- nowgoal_odds / nowgoal_schedule / fotmob_* ...
  subject      TEXT NOT NULL,           -- match_id / date / league_id(与 poll_state 同义)
  attempted_at TEXT NOT NULL,           -- UTC ISO(轮询判定所用的 now)
  tier         TEXT,                    -- 命中的节流档(le_1h/checkpoint_24h/last_call/...)可空
  ok           INTEGER NOT NULL DEFAULT 1,
  poll_run_id  TEXT
);

CREATE INDEX idx_poll_attempt_subject ON poll_attempt_log(source, subject, attempted_at DESC);
CREATE INDEX idx_poll_attempt_time ON poll_attempt_log(attempted_at DESC);
