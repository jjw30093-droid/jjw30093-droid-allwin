-- 轮询到期状态持久化(CLAUDE.md §6.3):
-- Worker 每 5 分钟触发到期判断;真正的采集频率由本表 + 采集窗口共同约束,
-- 进程重启不得造成无界重复采集。

CREATE TABLE poll_state (
  source           TEXT NOT NULL,   -- nowgoal_odds / nowgoal_schedule / fotmob_snapshot
  subject          TEXT NOT NULL,   -- fotmob_match_id / 日程日期(YYYY-MM-DD)等
  last_polled_at   TEXT NOT NULL,   -- UTC ISO8601
  last_poll_run_id TEXT,
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (source, subject)
);
