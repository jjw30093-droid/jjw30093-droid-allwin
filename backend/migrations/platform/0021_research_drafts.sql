-- Local research workflow; existing draft and export tables remain the UI contract.
CREATE TABLE research_draft_links (
  draft_id TEXT PRIMARY KEY REFERENCES content_drafts(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  snapshot_id TEXT NOT NULL CHECK(length(snapshot_id)=64),
  packet_sha256 TEXT NOT NULL CHECK(length(packet_sha256)=64),
  revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
  UNIQUE(user_id, snapshot_id)
);
CREATE TABLE research_draft_events (
  id TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL REFERENCES research_draft_links(draft_id),
  actor_id TEXT NOT NULL REFERENCES users(id),
  revision INTEGER NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('import','edit','review','reopen')),
  content_hash TEXT NOT NULL,
  content_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(draft_id, revision)
);
CREATE TRIGGER research_events_no_update BEFORE UPDATE ON research_draft_events
BEGIN SELECT RAISE(ABORT, 'research review history is immutable'); END;
CREATE TRIGGER research_events_no_delete BEFORE DELETE ON research_draft_events
BEGIN SELECT RAISE(ABORT, 'research review history is immutable'); END;
CREATE TRIGGER research_links_identity BEFORE UPDATE ON research_draft_links
WHEN NEW.draft_id != OLD.draft_id OR NEW.user_id != OLD.user_id
  OR NEW.snapshot_id != OLD.snapshot_id OR NEW.packet_sha256 != OLD.packet_sha256
BEGIN SELECT RAISE(ABORT, 'research source identity is immutable'); END;
CREATE TRIGGER research_links_no_delete BEFORE DELETE ON research_draft_links
BEGIN SELECT RAISE(ABORT, 'research source link is immutable'); END;
CREATE TRIGGER research_bundle_immutable BEFORE UPDATE ON content_drafts
WHEN EXISTS(SELECT 1 FROM research_draft_links WHERE draft_id=OLD.id)
 AND (NEW.bundle_json != OLD.bundle_json OR NEW.user_id != OLD.user_id
      OR NEW.match_id != OLD.match_id OR NEW.id != OLD.id)
BEGIN SELECT RAISE(ABORT, 'research evidence is immutable'); END;
