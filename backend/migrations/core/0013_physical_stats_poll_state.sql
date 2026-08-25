-- 0013: 体能统计(physical_metrics_*)迟到补采的检查点状态。
--
-- 背景见 backend/ingest/physical_stats_poll.py 顶部文档:FotMob 部分体能统计
-- 是异步计算的,常在比赛完赛后才出现最终值,现有管道只在比赛刚解决时抓一次
-- 就不再回访。本表记录"这场比赛的体能统计回查做到第几次检查点了"。
--
-- 落在 core(allwin.db)而非 odds.db:与 postmatch_retry_state(0010,落
-- odds.db)不同——那张表是 fotmob_incremental_multi/poll_windows 这一整套
-- odds.db 节流基础设施(poll_state/poll_attempt_log)的延伸,记的是"同一个
-- 采集任务的重试计数",同库不是巧合而是"同一基础设施不另起第三处"的直接
-- 结论。本表要回查、要校验的目标数据(fact_team_match_stats.extra_json 里
-- 的 physical_metrics_distance_covered)本身就在 core(allwin.db),这个
-- 任务也完全不触碰 odds.db 的任何表——把它硬塞进 odds.db 才是真正的
-- cargo-cult(照抄"重试状态住 odds.db"这个表面模式,却忽略了它成立的前提
-- 是"该任务本来就属于 odds.db 的节流基础设施")。数据离得越近,越不容易在
-- 之后的重构里因为"这张表明明是关于 core 数据的却查另一个库"而踩坑。

CREATE TABLE physical_stats_poll_state (
  match_id         INTEGER PRIMARY KEY,
  league_id        INTEGER NOT NULL,
  kickoff_at_utc   TEXT NOT NULL,
  checks_done      INTEGER NOT NULL DEFAULT 0,
  last_checkpoint  INTEGER,
  last_checked_at  TEXT,
  resolved_at      TEXT,
  exhausted_at     TEXT,
  fail_reason      TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE INDEX idx_physical_stats_poll_league
  ON physical_stats_poll_state(league_id, resolved_at, exhausted_at);
