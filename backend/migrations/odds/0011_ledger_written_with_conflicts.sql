-- 0011: fixture_sync_ledger.verdict 增加取值 written_with_conflicts。
--
-- 背景(2026-09-07 真实事故):荷甲(57)自 2026-09-06T06:06 起连续 4 次赛程同步
-- 全部 refused_downgrade、written_rows=0——**整个联赛的赛程同步停摆 24 小时**,
-- 每次抓回 265 行一行没写进去。肇事的是 FotMob 一条自相矛盾的记录
-- (match 5781733):同一个对象里既带比分(home 1 / away 3)、又标
-- notStarted:true、finished:false,还把开球从 09-05 16:45Z 改挂到 09-08 12:00Z。
-- 这种脏数据不会自愈,旧的"整批拒写"会让该联赛**无限期**冻结。
--
-- 写入端(backend/cli/sync_fixtures_window.py)因此改为**逐行剔除**冲突行、
-- 其余照常写入。安全性一字未变:已完赛/已有比分的行仍然绝不被未完赛行覆盖,
-- 只是从"拒绝整批"改成"把这几行排除在写入集合之外"。
--
-- 新 verdict 的语义:
--   written_with_conflicts  联赛照常同步,但有若干行因为会造成降级而被剔除;
--                           被剔除的 Match_ID 全部记在 detail 里(不静默丢弃,
--                           CLAUDE.md §13),由 pipeline_gates 的 G2 以 WARNING
--                           暴露(整批拒写仍是 CRITICAL)。
--
-- refused_downgrade 保留在取值集合里:历史 ledger 行仍带它,不做破坏性改写;
-- 写入端从此不再产生这个值。
--
-- SQLite 改不了 CHECK 约束,只能重建表再搬数据(与 platform/0014 同一手法)。

CREATE TABLE fixture_sync_ledger_new (
  id                        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at                    TEXT NOT NULL,
  poll_run_id               TEXT,
  league_id                 INTEGER NOT NULL,
  season                    TEXT,
  provider_selected_season  TEXT,
  fallback_season_used      INTEGER NOT NULL DEFAULT 0,
  fetched_rows              INTEGER NOT NULL DEFAULT 0,
  horizon7_rows             INTEGER NOT NULL DEFAULT 0,
  written_rows              INTEGER NOT NULL DEFAULT 0,
  prev_fetched_rows         INTEGER,
  verdict                   TEXT NOT NULL CHECK (verdict IN
    ('written','written_with_conflicts','refused_regression','refused_downgrade',
     'refused_identity','off_season','fetch_failed')),
  detail                    TEXT
);

INSERT INTO fixture_sync_ledger_new
  (id, run_at, poll_run_id, league_id, season, provider_selected_season,
   fallback_season_used, fetched_rows, horizon7_rows, written_rows,
   prev_fetched_rows, verdict, detail)
SELECT
   id, run_at, poll_run_id, league_id, season, provider_selected_season,
   fallback_season_used, fetched_rows, horizon7_rows, written_rows,
   prev_fetched_rows, verdict, detail
FROM fixture_sync_ledger;

DROP TABLE fixture_sync_ledger;
ALTER TABLE fixture_sync_ledger_new RENAME TO fixture_sync_ledger;

CREATE INDEX idx_fixture_ledger_league ON fixture_sync_ledger(league_id, run_at DESC);
CREATE INDEX idx_fixture_ledger_verdict ON fixture_sync_ledger(verdict, run_at DESC);
