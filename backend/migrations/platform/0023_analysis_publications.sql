CREATE TABLE analysis_articles (
 id TEXT PRIMARY KEY,
 draft_id TEXT NOT NULL UNIQUE REFERENCES content_drafts(id),
 owner_id TEXT NOT NULL REFERENCES users(id),
 slug TEXT NOT NULL UNIQUE,
 publication_version INTEGER NOT NULL DEFAULT 0 CHECK(publication_version>=0),
 current_revision_id TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 FOREIGN KEY(id,current_revision_id) REFERENCES analysis_article_versions(article_id,id)
);
CREATE TABLE analysis_article_versions (
 id TEXT PRIMARY KEY,
 article_id TEXT NOT NULL REFERENCES analysis_articles(id),
 publication_version INTEGER NOT NULL CHECK(publication_version>0),
 source_revision INTEGER NOT NULL,
 review_event_id TEXT NOT NULL REFERENCES research_draft_events(id),
 title TEXT NOT NULL,
 excerpt TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('prematch','postmatch','round')),
 visibility TEXT NOT NULL CHECK(visibility IN ('public','member')),
 content_json TEXT NOT NULL CHECK(json_valid(content_json)),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 published_at TEXT NOT NULL,
 UNIQUE(article_id,id), UNIQUE(article_id,publication_version)
);
CREATE TABLE analysis_publication_events (
 id TEXT PRIMARY KEY,
 article_id TEXT NOT NULL REFERENCES analysis_articles(id),
 publication_version INTEGER NOT NULL,
 actor_id TEXT NOT NULL REFERENCES users(id),
 action TEXT NOT NULL CHECK(action IN ('publish','retract')),
 revision_id TEXT,
 reason TEXT NOT NULL CHECK(length(trim(reason))>0),
 created_at TEXT NOT NULL,
 UNIQUE(article_id,publication_version),
 FOREIGN KEY(article_id,revision_id) REFERENCES analysis_article_versions(article_id,id)
);
CREATE INDEX analysis_articles_current ON analysis_articles(updated_at DESC) WHERE current_revision_id IS NOT NULL;
CREATE TRIGGER analysis_versions_no_update BEFORE UPDATE ON analysis_article_versions
BEGIN SELECT RAISE(ABORT,'published version is immutable'); END;
CREATE TRIGGER analysis_versions_no_delete BEFORE DELETE ON analysis_article_versions
BEGIN SELECT RAISE(ABORT,'published version is immutable'); END;
CREATE TRIGGER analysis_publication_events_no_update BEFORE UPDATE ON analysis_publication_events
BEGIN SELECT RAISE(ABORT,'publication history is immutable'); END;
CREATE TRIGGER analysis_publication_events_no_delete BEFORE DELETE ON analysis_publication_events
BEGIN SELECT RAISE(ABORT,'publication history is immutable'); END;
