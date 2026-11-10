-- Reuse content_drafts, research review history, job_runs and export_jobs.
CREATE TABLE IF NOT EXISTS analyst_report_links (
  draft_id TEXT PRIMARY KEY REFERENCES research_draft_links(draft_id),
  match_id TEXT NOT NULL,
  phase TEXT NOT NULL CHECK(phase IN ('prematch','postmatch')),
  source_revision TEXT NOT NULL,
  previous_draft_id TEXT REFERENCES research_draft_links(draft_id),
  prematch_draft_id TEXT REFERENCES research_draft_links(draft_id),
  captured_at TEXT NOT NULL,
  kickoff_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS analyst_report_match ON analyst_report_links(match_id,phase,captured_at);
CREATE TRIGGER IF NOT EXISTS analyst_links_immutable BEFORE UPDATE ON analyst_report_links
BEGIN SELECT RAISE(ABORT,'analyst report links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS analyst_links_no_delete BEFORE DELETE ON analyst_report_links
BEGIN SELECT RAISE(ABORT,'analyst report links are immutable'); END;
