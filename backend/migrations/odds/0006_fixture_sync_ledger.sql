-- 0006: T+7 赛程同步账本(数据管道重建 Phase 2)。
--
-- 每次逐联赛赛程同步落一行:抓了多少、写了多少、哪个门禁拒绝了、为什么。
-- 作用:①反退化门禁 G-A 的"上一次成功抓取"基线来源;②质量门 G1 的数据源;
-- ③"某联赛赛程为什么没更新"的审计证据。放 odds.db(CLAUDE.md §5.2:
-- 采集运行和数据质量结果)。
--
-- verdict 语义:
--   written             正常写入
--   refused_regression  较上次成功抓取骤降 >50%(前身项目事故 #5/#7:部分数据
--                       覆盖好数据、慢性失血),拒写并保留旧数据
--   refused_downgrade   来源试图把已完赛行降级回未完赛 / 已有比分的行被赛程行
--                       覆盖(事故 #2 的清列同型),拒写整批
--   refused_identity    details.id 与请求联赛不符(SeasonIdentityError)
--   off_season          该联赛当前无未来赛程(欧战夏休等),合法空,不告警
--   fetch_failed        网络/解析失败,如实记录(不伪装成 off_season)
--
-- 节流状态不在本表(在 poll_state,source='fotmob_fixtures');
-- not_due 的空转不落行,保持本表高信噪。

CREATE TABLE fixture_sync_ledger (
  id                        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at                    TEXT NOT NULL,          -- UTC ISO
  poll_run_id               TEXT,
  league_id                 INTEGER NOT NULL,
  season                    TEXT,                   -- 实际采用的赛季串(发现值)
  provider_selected_season  TEXT,                   -- 响应 details.selectedSeason 原样
  fallback_season_used      INTEGER NOT NULL DEFAULT 0,
  fetched_rows              INTEGER NOT NULL DEFAULT 0,  -- 本次抓到的未完赛行
  horizon7_rows             INTEGER NOT NULL DEFAULT 0,  -- 其中落在 T+7 窗口内的
  written_rows              INTEGER NOT NULL DEFAULT 0,
  prev_fetched_rows         INTEGER,                -- G-A 对比基线(上次 written 行)
  verdict                   TEXT NOT NULL CHECK (verdict IN
    ('written','refused_regression','refused_downgrade',
     'refused_identity','off_season','fetch_failed')),
  detail                    TEXT
);
CREATE INDEX idx_fixture_ledger_league ON fixture_sync_ledger(league_id, run_at DESC);
CREATE INDEX idx_fixture_ledger_verdict ON fixture_sync_ledger(verdict, run_at DESC);
