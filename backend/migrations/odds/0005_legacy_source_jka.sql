-- 扩展 bronze_legacy_odds_summary.source 允许值,接入 J1/韩K联/澳超历史赔率
-- (2026-08-07,docs/current-state.md §26/§27):
--
-- - 'football_uk_jka':旧库 football_uk.db silver_match_odds 的 J/K/A 子集,
--   标签写着 footballdata 但**已实证为 NowGoal 数据**(11 场跨联赛跨年份实爬
--   对照 66 组数值全部精确一致;与同一张旧表的五大联赛子集不同——那部分是
--   真 football-data.co.uk CSV,AH 线符号相反、入库时取反过)。本子集符号
--   约定与本表 canonical(line>0=主队让球)一致,入库**不取反**,
--   orientation_fixed=0。
-- - 'nowgoal_archive_refetch':NowGoal season archive 实爬的两点摘要
--   (2026 年 4 月尾部 32 场,旧库该批行开盘缺线/收盘仅有盘中过时快照)。
--   archive 逐行带时间戳,开/收盘按 kickoff 前最早/最晚观测点截取;
--   但本表仍只存两点摘要,不带时间戳字段,不冒充完整时间线。
--
-- SQLite 无法 ALTER CHECK,按标准重建流程迁移,保留全部既有行。

CREATE TABLE bronze_legacy_odds_summary_new (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  fotmob_match_id   INTEGER NOT NULL,
  source            TEXT NOT NULL CHECK (source IN (
                      'asset_a_json','asset_b_footballdata','asset_b_nowgoal',
                      'football_uk_jka','nowgoal_archive_refetch')),
  provider          TEXT NOT NULL,
  market            TEXT NOT NULL CHECK (market IN ('1x2','ah','ou')),
  period            TEXT NOT NULL CHECK (period IN ('initial','latest')),
  line              REAL,
  home_or_over      REAL NOT NULL,
  draw              REAL,
  away_or_under     REAL NOT NULL,
  orientation_fixed INTEGER NOT NULL DEFAULT 0,
  source_file       TEXT,
  ingested_at       TEXT NOT NULL,
  UNIQUE (fotmob_match_id, source, market, period)
);

INSERT INTO bronze_legacy_odds_summary_new
  (id, fotmob_match_id, source, provider, market, period, line,
   home_or_over, draw, away_or_under, orientation_fixed, source_file, ingested_at)
SELECT id, fotmob_match_id, source, provider, market, period, line,
       home_or_over, draw, away_or_under, orientation_fixed, source_file, ingested_at
FROM bronze_legacy_odds_summary;

DROP TABLE bronze_legacy_odds_summary;
ALTER TABLE bronze_legacy_odds_summary_new RENAME TO bronze_legacy_odds_summary;
CREATE INDEX idx_legacy_odds_match ON bronze_legacy_odds_summary(fotmob_match_id);
