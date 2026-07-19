-- platform.db 初始 schema(用户/身份/会话/套餐/订阅/预测登记簿/内容/任务/审计)
-- 全部时间戳为 UTC ISO8601 文本,如 2026-07-19T12:00:00Z(CLAUDE.md §6.2)

CREATE TABLE roles (
  name        TEXT PRIMARY KEY,
  description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE users (
  id            TEXT PRIMARY KEY,              -- 内部 UUID,唯一用户主键(CLAUDE.md §7.1)
  display_name  TEXT NOT NULL,
  avatar_url    TEXT,
  role          TEXT NOT NULL DEFAULT 'user' REFERENCES roles(name),
  password_hash TEXT,                          -- 仅 CLI 创建的 admin 使用(Argon2)
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE auth_identities (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id          TEXT NOT NULL REFERENCES users(id),
  provider         TEXT NOT NULL,              -- wechat_oa / wechat_open / password / email / phone / mock
  provider_app_id  TEXT NOT NULL DEFAULT '',
  provider_subject TEXT NOT NULL,              -- OpenID / username 等外部主体
  union_id         TEXT,                       -- 仅微信真实返回时保存,不得推测
  created_at       TEXT NOT NULL,
  last_used_at     TEXT,
  UNIQUE (provider, provider_app_id, provider_subject)
);
CREATE INDEX idx_auth_identities_user ON auth_identities(user_id);
CREATE INDEX idx_auth_identities_union ON auth_identities(union_id) WHERE union_id IS NOT NULL;

CREATE TABLE auth_sessions (
  id              TEXT PRIMARY KEY,            -- 会话 UUID(可撤销标识)
  user_id         TEXT NOT NULL REFERENCES users(id),
  token_hash      TEXT NOT NULL UNIQUE,        -- 原始 token ≥256bit,只存 SHA-256
  csrf_token_hash TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  expires_at      TEXT NOT NULL,
  last_seen_at    TEXT,
  revoked_at      TEXT,
  user_agent      TEXT,
  ip_hash         TEXT                         -- 不存完整 IP
);
CREATE INDEX idx_auth_sessions_user ON auth_sessions(user_id);
CREATE INDEX idx_auth_sessions_expires ON auth_sessions(expires_at);

CREATE TABLE oauth_states (
  state_hash        TEXT PRIMARY KEY,          -- state 原值只存 hash,一次性消费
  kind              TEXT NOT NULL CHECK (kind IN ('login','device_approve')),
  next_path         TEXT NOT NULL DEFAULT '/',
  device_request_id TEXT,
  created_at        TEXT NOT NULL,
  expires_at        TEXT NOT NULL,
  used_at           TEXT
);

CREATE TABLE device_login_requests (
  id          TEXT PRIMARY KEY,                -- 公开 request id(进二维码)
  secret_hash TEXT NOT NULL,                   -- 浏览器 secret 的 SHA-256(绝不进二维码)
  status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','claimed','expired','cancelled')),
  user_id     TEXT REFERENCES users(id),
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  approved_at TEXT,
  claimed_at  TEXT
);

CREATE TABLE account_links (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    TEXT NOT NULL REFERENCES users(id),
  kind       TEXT NOT NULL,                    -- recovery_email / recovery_phone / note
  value      TEXT NOT NULL,
  verified   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_account_links_user ON account_links(user_id);

-- ── 套餐与权益 ──────────────────────────────────────────────

CREATE TABLE plans (
  id          TEXT PRIMARY KEY,                -- free / pro / premium
  name_zh     TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  rank        INTEGER NOT NULL DEFAULT 0,      -- free=0 pro=1 premium=2
  is_active   INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);

CREATE TABLE plan_entitlements (
  plan_id     TEXT NOT NULL REFERENCES plans(id),
  entitlement TEXT NOT NULL,
  PRIMARY KEY (plan_id, entitlement)
);

CREATE TABLE products (
  id            TEXT PRIMARY KEY,              -- pro_monthly 等 SKU
  plan_id       TEXT NOT NULL REFERENCES plans(id),
  name_zh       TEXT NOT NULL,
  description   TEXT NOT NULL DEFAULT '',
  duration_days INTEGER NOT NULL,
  price_cents   INTEGER NOT NULL,
  currency      TEXT NOT NULL DEFAULT 'CNY',
  is_active     INTEGER NOT NULL DEFAULT 1,
  sort_order    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);

CREATE TABLE subscriptions (
  id         TEXT PRIMARY KEY,                 -- UUID
  user_id    TEXT NOT NULL REFERENCES users(id),
  plan_id    TEXT NOT NULL REFERENCES plans(id),
  status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
  starts_at  TEXT NOT NULL,
  ends_at    TEXT NOT NULL,
  source     TEXT NOT NULL CHECK (source IN ('admin_grant','redeem_code','payment')),
  source_ref TEXT,
  granted_by TEXT REFERENCES users(id),
  notes      TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_subscriptions_user ON subscriptions(user_id, ends_at);

CREATE TABLE redeem_codes (
  id            TEXT PRIMARY KEY,              -- UUID
  code_hash     TEXT NOT NULL UNIQUE,          -- 兑换码只存 SHA-256,明文仅生成时展示一次
  plan_id       TEXT NOT NULL REFERENCES plans(id),
  duration_days INTEGER NOT NULL,
  batch_id      TEXT,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','used','revoked','expired')),
  created_by    TEXT REFERENCES users(id),
  created_at    TEXT NOT NULL,
  expires_at    TEXT,
  used_by       TEXT REFERENCES users(id),
  used_at       TEXT
);
CREATE INDEX idx_redeem_codes_batch ON redeem_codes(batch_id) WHERE batch_id IS NOT NULL;

-- ── 预测登记簿(CLAUDE.md §9:不可覆盖) ─────────────────────

CREATE TABLE model_versions (
  id           TEXT PRIMARY KEY,               -- 如 dc-baseline-1.M.2
  algorithm    TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  params_json  TEXT NOT NULL DEFAULT '{}',
  trained_at   TEXT,
  train_range  TEXT,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL
);

CREATE TABLE prediction_runs (
  id               TEXT PRIMARY KEY,
  model_version_id TEXT NOT NULL REFERENCES model_versions(id),
  triggered_by     TEXT NOT NULL DEFAULT 'manual' CHECK (triggered_by IN ('manual','worker','import')),
  input_cutoff_at  TEXT,
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  status           TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','succeeded','failed')),
  matches_count    INTEGER NOT NULL DEFAULT 0,
  notes            TEXT NOT NULL DEFAULT '',
  created_at       TEXT NOT NULL
);

CREATE TABLE prediction_snapshots (
  id                  TEXT PRIMARY KEY,        -- UUID
  run_id              TEXT REFERENCES prediction_runs(id),
  match_id            INTEGER NOT NULL,        -- FotMob match id(allwin.db dim_match)
  kickoff_at_utc      TEXT,                    -- 冗余存储,用于赛前判定
  model_version_id    TEXT NOT NULL REFERENCES model_versions(id),
  generated_at        TEXT NOT NULL,
  published_at        TEXT,
  locked_at           TEXT,
  input_cutoff_at     TEXT,
  input_snapshot_hash TEXT,
  prediction_hash     TEXT NOT NULL,
  home_win            REAL NOT NULL,
  draw                REAL NOT NULL,
  away_win            REAL NOT NULL,
  expected_home_goals REAL,
  expected_away_goals REAL,
  confidence          TEXT,
  visibility          TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public','member','internal')),
  status              TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','locked','retracted','legacy_unverified')),
  is_official         INTEGER NOT NULL DEFAULT 0,
  superseded_by       TEXT REFERENCES prediction_snapshots(id),
  created_at          TEXT NOT NULL,
  CHECK (home_win >= 0 AND draw >= 0 AND away_win >= 0),
  CHECK (abs(home_win + draw + away_win - 1.0) < 0.001)
);
CREATE INDEX idx_pred_snap_match ON prediction_snapshots(match_id);
CREATE INDEX idx_pred_snap_status ON prediction_snapshots(status, is_official);
CREATE INDEX idx_pred_snap_kickoff ON prediction_snapshots(kickoff_at_utc);

-- 锁定后禁止改动实质字段(数据库层强制,service 层另有校验)
CREATE TRIGGER trg_pred_snap_locked_immutable
BEFORE UPDATE OF match_id, model_version_id, run_id, generated_at, input_cutoff_at,
                 input_snapshot_hash, prediction_hash,
                 home_win, draw, away_win, expected_home_goals, expected_away_goals,
                 published_at, locked_at, kickoff_at_utc, is_official, visibility
ON prediction_snapshots
WHEN OLD.locked_at IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'locked prediction snapshot is immutable');
END;

-- 正式/锁定预测禁止物理删除,只允许撤回状态
CREATE TRIGGER trg_pred_snap_no_delete
BEFORE DELETE ON prediction_snapshots
WHEN OLD.locked_at IS NOT NULL OR OLD.is_official = 1
BEGIN
  SELECT RAISE(ABORT, 'official/locked prediction snapshots cannot be deleted');
END;

CREATE TABLE prediction_outcomes (
  match_id    INTEGER PRIMARY KEY,
  home_goals  INTEGER NOT NULL,
  away_goals  INTEGER NOT NULL,
  outcome     TEXT NOT NULL CHECK (outcome IN ('home','draw','away')),
  finished_at TEXT,
  settled_at  TEXT NOT NULL,
  source      TEXT NOT NULL DEFAULT 'fotmob'
);

CREATE TABLE prediction_evaluations (
  id               TEXT PRIMARY KEY,
  model_version_id TEXT REFERENCES model_versions(id),
  scope_json       TEXT NOT NULL DEFAULT '{}', -- 样本口径(official_only/league/时间窗等)
  sample_size      INTEGER NOT NULL,
  accuracy         REAL,
  brier            REAL,
  log_loss         REAL,
  rps              REAL,
  calibration_json TEXT NOT NULL DEFAULT '[]',
  evaluated_at     TEXT NOT NULL,
  notes            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE prediction_manifests (
  id            TEXT PRIMARY KEY,
  manifest_date TEXT NOT NULL,                 -- YYYY-MM-DD (UTC)
  version       INTEGER NOT NULL DEFAULT 1,
  manifest_json TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  s3_key        TEXT,
  uploaded_at   TEXT,
  UNIQUE (manifest_date, version)
);

-- ── 用户内容与任务 ─────────────────────────────────────────

CREATE TABLE favorites (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    TEXT NOT NULL REFERENCES users(id),
  match_id   INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (user_id, match_id)
);

CREATE TABLE content_drafts (
  id             TEXT PRIMARY KEY,
  user_id        TEXT NOT NULL REFERENCES users(id),
  match_id       INTEGER NOT NULL,
  title          TEXT NOT NULL DEFAULT '',
  bundle_json    TEXT NOT NULL,                -- 冻结的 analysis_bundle 快照
  overrides_json TEXT NOT NULL DEFAULT '{}',   -- 用户编辑(标题/证据/风险/口播稿)
  status         TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','reviewed','published')),
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX idx_content_drafts_user ON content_drafts(user_id, updated_at);

CREATE TABLE export_jobs (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id),
  draft_id    TEXT REFERENCES content_drafts(id),
  match_id    INTEGER,
  kind        TEXT NOT NULL,                   -- png_1080x1920 / png_1080x1350 / txt / json / srt
  status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','failed')),
  file_path   TEXT,
  error       TEXT,
  meta_json   TEXT NOT NULL DEFAULT '{}',
  created_at  TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX idx_export_jobs_user ON export_jobs(user_id, created_at);

CREATE TABLE job_runs (
  id              TEXT PRIMARY KEY,
  job_name        TEXT NOT NULL,
  idempotency_key TEXT,
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','failed','skipped')),
  attempt         INTEGER NOT NULL DEFAULT 1,
  max_attempts    INTEGER NOT NULL DEFAULT 1,
  started_at      TEXT,
  finished_at     TEXT,
  input_count     INTEGER,
  output_count    INTEGER,
  error_summary   TEXT,
  meta_json       TEXT NOT NULL DEFAULT '{}',
  created_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_job_runs_idem ON job_runs(job_name, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_job_runs_name ON job_runs(job_name, created_at);

CREATE TABLE audit_logs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_user_id TEXT REFERENCES users(id),
  actor_type    TEXT NOT NULL DEFAULT 'admin' CHECK (actor_type IN ('admin','system','user')),
  action        TEXT NOT NULL,
  target_type   TEXT,
  target_id     TEXT,
  detail_json   TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL
);
CREATE INDEX idx_audit_logs_created ON audit_logs(created_at);
CREATE INDEX idx_audit_logs_actor ON audit_logs(actor_user_id, created_at);

CREATE TABLE analytics_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  event      TEXT NOT NULL,
  anon_id    TEXT,                             -- 前端随机匿名 id,非 OpenID
  user_id    TEXT,                             -- 已登录时的内部 user id,可空
  path       TEXT,
  referrer   TEXT,
  meta_json  TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_analytics_event ON analytics_events(event, created_at);
