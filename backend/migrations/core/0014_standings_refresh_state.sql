-- 0014: 联赛积分榜(fact_league_table)迟到刷新的"上次刷新到哪"状态。
--
-- 背景(CLAUDE.md §6.3/§13,站长真实诊断):fact_league_table 只由
-- backend/ingest/ingest_league.py::ingest_season_tables() 写入,而该函数
-- 从未被任何 worker 任务调度过——只有手动 CLI 会调用它。一旦某赛季的
-- skeleton 行(played=0)落库,该函数在 CLI 路径里的 _season_tables_done()
-- 短路判断会让它此后再也不会被自动重跑,导致积分榜永久停在赛季初始值,
-- 即使比赛已经踢完多轮(2026-08 英超 2026/2027 真实事故,已手动修复一次,
-- 但不是持久修复)。
--
-- 本表服务于新的 allwin-standings.timer(backend/cli/poll_standings.py):
-- 记录"这个 (League_ID, Season) 上一次真正把积分榜刷新到什么时间"(
-- last_refreshed_at)以及"刷新时看到的、触发这次刷新的最近一场完赛比赛的
-- 开球时间"(last_finished_kickoff_at_utc)——判断"现在是否该再刷新一次"
-- 只需要比较"最近一场完赛比赛开球时间 + 6 小时"与 last_refreshed_at 的
-- 先后关系(见 backend/ingest/standings_refresh_poll.py::due_refresh 的
-- 纯函数定义),不需要更多字段。
--
-- 落在 core(allwin.db)而非 odds.db:fact_league_table 本身就在 core,
-- 这个任务完全不碰 odds.db 的任何表——与 0013(physical_stats_poll_state)
-- 同一条"数据离得越近越不容易在重构里查错库"的理由,不是巧合。
--
-- 主键用 (League_ID, Season) 而不是单纯 League_ID:虽然当前只有英超一个
-- 联赛在范围内,但同一联赛跨赛季是常态(赛季切换后旧赛季的状态行应该
-- 保留而不是被覆盖,便于审计"上赛季最后一次刷新是什么时候")。

CREATE TABLE standings_refresh_state (
  league_id                   INTEGER NOT NULL,
  season                      TEXT NOT NULL,
  last_refreshed_at           TEXT,
  last_finished_kickoff_at_utc TEXT,
  created_at                  TEXT NOT NULL,
  updated_at                  TEXT NOT NULL,
  PRIMARY KEY (league_id, season)
);
