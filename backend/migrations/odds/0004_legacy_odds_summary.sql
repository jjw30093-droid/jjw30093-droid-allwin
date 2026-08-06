-- 旧项目历史赔率摘要(仅初盘+临场两点,无观测时间戳)。
--
-- 为什么不进 bronze_ng_odds_snap:该表 observed_at NOT NULL,是真实观测时间序列;
-- 旧资产(miaomiaodi match_odds_data*.json / football_uk.db silver_match_odds)
-- 没有任何逐条观测时间——按 CLAUDE.md §6.2 不得用抓取时间伪装观测时间,
-- 也不能让 silver_odds_moves 从两点摘要里造出虚假变化点。单独建表,
-- API 层用 coverage_tier 区分 full_timeline / open_close_only。
--
-- 符号统一约定(入库前由 ingest CLI 归一,详见 backend/cli/ingest_legacy_odds.py):
-- - 主客方向 = FotMob canonical(dim_match 的 Home/Away);
-- - ah line>0 表示主队让球(主队是热门),与 bronze_ng_odds_snap canonical 一致;
-- - 1x2 的 home/draw/away 同为 canonical 方向。

CREATE TABLE bronze_legacy_odds_summary (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  fotmob_match_id   INTEGER NOT NULL,
  source            TEXT NOT NULL CHECK (source IN ('asset_a_json','asset_b_footballdata','asset_b_nowgoal')),
  provider          TEXT NOT NULL,               -- Bet365 等(旧资产单公司)
  market            TEXT NOT NULL CHECK (market IN ('1x2','ah','ou')),
  period            TEXT NOT NULL CHECK (period IN ('initial','latest')),
  line              REAL,                        -- ah/ou 盘口线;1x2 恒 NULL
  home_or_over      REAL NOT NULL,
  draw              REAL,                        -- 仅 1x2
  away_or_under     REAL NOT NULL,
  orientation_fixed INTEGER NOT NULL DEFAULT 0,  -- 1=入库时应用过主客反转/符号修正
  source_file       TEXT,                        -- 溯源:来自哪个旧文件/表
  ingested_at       TEXT NOT NULL,
  UNIQUE (fotmob_match_id, source, market, period)
);

CREATE INDEX idx_legacy_odds_match ON bronze_legacy_odds_summary(fotmob_match_id);
