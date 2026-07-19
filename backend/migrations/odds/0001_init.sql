-- odds.db 初始 schema(实体映射 / NowGoal 快照 / FotMob 阵容伤停快照 / 变化点 / 时间共现 / 源健康)
-- 时间戳约定(CLAUDE.md §6.2):
--   source_updated_at 来源声明时间(来源未声明必须为 NULL,不得用抓取时间伪装)
--   observed_at       本系统首次观察到变化的 UTC 时间
--   ingested_at       数据库写入 UTC 时间
--   poll_run_id       同一轮采集标识

CREATE TABLE dim_team_xref (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  provider          TEXT NOT NULL,             -- nowgoal 等
  provider_team_id  TEXT NOT NULL,
  canonical_team_id INTEGER NOT NULL,          -- canonical = FotMob Team_ID
  confidence        REAL NOT NULL DEFAULT 0,
  verified          INTEGER NOT NULL DEFAULT 0,
  method            TEXT NOT NULL DEFAULT 'auto' CHECK (method IN ('auto','manual')),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  UNIQUE (provider, provider_team_id)
);
CREATE INDEX idx_team_xref_canonical ON dim_team_xref(canonical_team_id);

CREATE TABLE dim_team_alias (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_team_id INTEGER NOT NULL,
  alias             TEXT NOT NULL,
  source            TEXT NOT NULL DEFAULT '',
  created_at        TEXT NOT NULL,
  UNIQUE (canonical_team_id, alias)
);
CREATE INDEX idx_team_alias_alias ON dim_team_alias(alias);

CREATE TABLE dim_match_xref (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  fotmob_match_id      INTEGER NOT NULL,
  provider             TEXT NOT NULL DEFAULT 'nowgoal',
  provider_match_id    TEXT NOT NULL,
  home_away_inverted   INTEGER NOT NULL DEFAULT 0,
  confidence           REAL NOT NULL DEFAULT 0,
  verified             INTEGER NOT NULL DEFAULT 0,
  method               TEXT NOT NULL DEFAULT 'auto' CHECK (method IN ('auto','manual')),
  kickoff_diff_seconds INTEGER,
  review_status        TEXT NOT NULL DEFAULT 'needs_review'
                       CHECK (review_status IN ('auto_ok','needs_review','confirmed','rejected')),
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  UNIQUE (provider, provider_match_id),
  UNIQUE (provider, fotmob_match_id)
);

CREATE TABLE bronze_ng_odds_snap (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_match_id TEXT NOT NULL,
  market            TEXT NOT NULL CHECK (market IN ('1x2','ah','ou')),
  company_id        TEXT NOT NULL,
  company_name      TEXT NOT NULL DEFAULT '',
  market_phase      TEXT NOT NULL CHECK (market_phase IN ('pre_match','in_play','unknown')),
  payload_json      TEXT NOT NULL,
  payload_hash      TEXT NOT NULL,             -- canonical payload hash,变化才落库(代码层判定)
  source_updated_at TEXT,
  observed_at       TEXT NOT NULL,
  ingested_at       TEXT NOT NULL,
  poll_run_id       TEXT
);
CREATE INDEX idx_ng_snap_series ON bronze_ng_odds_snap(provider_match_id, market, company_id, observed_at);

CREATE TABLE bronze_fm_lineup_snap (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  fotmob_match_id   INTEGER NOT NULL,
  payload_json      TEXT NOT NULL,
  payload_hash      TEXT NOT NULL,
  source_updated_at TEXT,
  observed_at       TEXT NOT NULL,
  ingested_at       TEXT NOT NULL,
  poll_run_id       TEXT
);
CREATE INDEX idx_fm_lineup_match ON bronze_fm_lineup_snap(fotmob_match_id, observed_at);

CREATE TABLE bronze_fm_sideline_snap (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  fotmob_match_id   INTEGER NOT NULL,
  fotmob_team_id    INTEGER,
  payload_json      TEXT NOT NULL,
  payload_hash      TEXT NOT NULL,
  source_updated_at TEXT,
  observed_at       TEXT NOT NULL,
  ingested_at       TEXT NOT NULL,
  poll_run_id       TEXT
);
CREATE INDEX idx_fm_sideline_match ON bronze_fm_sideline_snap(fotmob_match_id, observed_at);

CREATE TABLE silver_odds_moves (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  fotmob_match_id  INTEGER NOT NULL,
  provider         TEXT NOT NULL DEFAULT 'nowgoal',
  company_id       TEXT NOT NULL,
  market           TEXT NOT NULL,
  field            TEXT NOT NULL DEFAULT '',   -- 变化的具体字段(home/draw/away/line 等)
  prev_value       TEXT,
  new_value        TEXT,
  from_snapshot_id INTEGER,
  to_snapshot_id   INTEGER NOT NULL,
  moved_at         TEXT NOT NULL,              -- 变化被观察到的时间(observed_at)
  created_at       TEXT NOT NULL,
  UNIQUE (to_snapshot_id, field)
);
CREATE INDEX idx_odds_moves_match ON silver_odds_moves(fotmob_match_id, moved_at);

CREATE TABLE silver_event_moves (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  fotmob_match_id  INTEGER NOT NULL,
  event_type       TEXT NOT NULL,              -- lineup_change / sideline_change
  detail_json      TEXT NOT NULL DEFAULT '{}',
  from_snapshot_id INTEGER,
  to_snapshot_id   INTEGER NOT NULL,
  moved_at         TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  UNIQUE (event_type, to_snapshot_id)
);
CREATE INDEX idx_event_moves_match ON silver_event_moves(fotmob_match_id, moved_at);

-- 时间共现:只表达"固定时间窗内同期发生",不声称因果(CLAUDE.md §6.4)
CREATE TABLE gold_move_cooccurrence (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  fotmob_match_id INTEGER NOT NULL,
  odds_move_id    INTEGER NOT NULL REFERENCES silver_odds_moves(id),
  event_move_id   INTEGER NOT NULL REFERENCES silver_event_moves(id),
  window_seconds  INTEGER NOT NULL,
  delta_seconds   INTEGER NOT NULL,            -- odds_move.moved_at - event_move.moved_at
  computed_at     TEXT NOT NULL,
  UNIQUE (odds_move_id, event_move_id, window_seconds)
);
CREATE INDEX idx_cooc_match ON gold_move_cooccurrence(fotmob_match_id);

CREATE TABLE source_health (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source        TEXT NOT NULL,                 -- nowgoal / fotmob_lineup / fotmob_sideline
  checked_at    TEXT NOT NULL,
  ok            INTEGER NOT NULL,
  latency_ms    INTEGER,
  error_summary TEXT,
  meta_json     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_source_health ON source_health(source, checked_at);
