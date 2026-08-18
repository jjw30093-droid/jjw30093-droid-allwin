-- 0010: 赛后完赛增量单比赛重试上限状态(PIPELINE_REDESIGN_V2 P4)。
--
-- fotmob_incremental_multi 的到期条件从"6h/联赛"节流改为事件驱动
-- (kickoff+2.5h 仍未 Finish 即到期)后,若某场比赛的 status 永远不会被
-- FotMob 翻成 Finish(数据源本身缺陷,不是我们能修的),该比赛会让所在
-- 联赛永久到期、无限重试。本表记录"这场比赛已反复检查过 N 次仍未解决"的
-- 持久计数(见 backend/ingest/postmatch_retry.py 的 MAX_ATTEMPTS),达到
-- 上限后置 exhausted_at + fail_reason,阻止该比赛继续单独触发到期(联赛仍
-- 会被 6h 兜底档连带覆盖,只是不再"专为这场比赛"打数据源),并经
-- backend.notify 推一次 CRITICAL 告警。
--
-- 落在 odds.db 而非 core/platform:fotmob_incremental 的节流状态(poll_state/
-- poll_attempt_log)一直落在 odds.db,本表是同一节流基础设施的延伸,不另起
-- 第三个存放位置。

CREATE TABLE postmatch_retry_state (
  match_id         INTEGER PRIMARY KEY,
  league_id        INTEGER NOT NULL,
  attempts         INTEGER NOT NULL DEFAULT 0,
  first_attempt_at TEXT NOT NULL,
  last_attempt_at  TEXT NOT NULL,
  exhausted_at     TEXT,
  fail_reason      TEXT
);

CREATE INDEX idx_postmatch_retry_league ON postmatch_retry_state(league_id, exhausted_at);
