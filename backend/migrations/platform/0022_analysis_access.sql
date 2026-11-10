-- Analysis-product rights are separate from existing VIP plans and match grants.
CREATE TABLE analysis_trials (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  starts_at TEXT NOT NULL,
  ends_at TEXT NOT NULL,
  CHECK(julianday(starts_at) IS NOT NULL AND julianday(ends_at) IS NOT NULL),
  CHECK(abs((julianday(ends_at)-julianday(starts_at))-3.0)<0.00000001)
);
CREATE TABLE analysis_memberships (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  status TEXT NOT NULL CHECK(status IN ('active','revoked')),
  starts_at TEXT NOT NULL,
  ends_at TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>0),
  updated_at TEXT NOT NULL,
  CHECK(julianday(starts_at) IS NOT NULL AND julianday(ends_at) IS NOT NULL
        AND julianday(ends_at)>julianday(starts_at))
);
CREATE TABLE analysis_membership_events (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  actor_id TEXT NOT NULL REFERENCES users(id),
  request_key TEXT NOT NULL,
  request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
  revision INTEGER NOT NULL CHECK(revision>0),
  action TEXT NOT NULL CHECK(action IN ('grant','revoke')),
  reason TEXT NOT NULL CHECK(length(trim(reason))>0),
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(actor_id,request_key),
  UNIQUE(user_id,revision)
);
CREATE TRIGGER analysis_trials_no_update BEFORE UPDATE ON analysis_trials
BEGIN SELECT RAISE(ABORT,'analysis trial is immutable'); END;
CREATE TRIGGER analysis_trials_no_delete BEFORE DELETE ON analysis_trials
BEGIN SELECT RAISE(ABORT,'analysis trial is immutable'); END;
CREATE TRIGGER analysis_events_no_update BEFORE UPDATE ON analysis_membership_events
BEGIN SELECT RAISE(ABORT,'analysis membership history is immutable'); END;
CREATE TRIGGER analysis_events_no_delete BEFORE DELETE ON analysis_membership_events
BEGIN SELECT RAISE(ABORT,'analysis membership history is immutable'); END;
