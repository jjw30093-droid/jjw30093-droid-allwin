-- Production schedule-state schema v1.
--
-- Real allwin.db has not applied this migration.  The v1 schema therefore
-- closes direct-SQL safety boundaries here, before any production rollout.
-- It coexists with legacy dim_match and does not alter/backfill legacy rows.
--
-- Canonical UTC text is fixed-width:
--   YYYY-MM-DDTHH:MM:SS.ffffffZ
-- Every event-time/order column validates this at the DB layer.

CREATE TABLE schedule_match_identity (
  id                  INTEGER PRIMARY KEY,
  provider            TEXT NOT NULL
                      CHECK (
                        length(provider) BETWEEN 1 AND 32
                        AND provider = trim(provider)
                        AND provider = lower(provider)
                        AND substr(provider, 1, 1) GLOB '[a-z]'
                        AND provider NOT GLOB '*[^a-z0-9_-]*'
                      ),
  provider_match_id   TEXT NOT NULL
                      CHECK (
                        length(provider_match_id) BETWEEN 1 AND 128
                        AND provider_match_id = trim(provider_match_id)
                        AND substr(provider_match_id, 1, 1)
                          GLOB '[A-Za-z0-9]'
                        AND provider_match_id
                          NOT GLOB '*[^A-Za-z0-9._:-]*'
                        AND (
                          provider_match_id GLOB '*[^0-9]*'
                          OR substr(provider_match_id, 1, 1) GLOB '[1-9]'
                        )
                      ),
  canonical_match_id  INTEGER REFERENCES dim_match(Match_ID),
  created_at          TEXT NOT NULL
                      CHECK (
                        COALESCE(
                          length(created_at) = 27
                          AND created_at GLOB
                            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                          AND CAST(substr(created_at, 1, 4) AS INTEGER)
                            BETWEEN 1 AND 9999
                          AND CAST(substr(created_at, 12, 2) AS INTEGER)
                            BETWEEN 0 AND 23
                          AND CAST(substr(created_at, 15, 2) AS INTEGER)
                            BETWEEN 0 AND 59
                          AND CAST(substr(created_at, 18, 2) AS INTEGER)
                            BETWEEN 0 AND 59
                          AND date(substr(created_at, 1, 10), '+0 days')
                            = substr(created_at, 1, 10),
                          0
                        )
                      ),
  identity_provenance TEXT NOT NULL CHECK (length(trim(identity_provenance)) > 0),
  CHECK (canonical_match_id IS NULL OR canonical_match_id > 0),
  UNIQUE (provider, provider_match_id)
);

CREATE UNIQUE INDEX uq_schedule_identity_provider_canonical
  ON schedule_match_identity(provider, canonical_match_id)
  WHERE canonical_match_id IS NOT NULL;

CREATE TRIGGER trg_schedule_identity_insert_guard
BEFORE INSERT ON schedule_match_identity
WHEN
  EXISTS (
    SELECT 1 FROM schedule_match_identity
    WHERE id = NEW.id
  )
  OR EXISTS (
    SELECT 1 FROM schedule_match_identity
    WHERE provider = NEW.provider
      AND provider_match_id = NEW.provider_match_id
  )
  OR (
    NEW.canonical_match_id IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM schedule_match_identity
      WHERE provider = NEW.provider
        AND canonical_match_id = NEW.canonical_match_id
    )
  )
BEGIN
  SELECT RAISE(ABORT, 'schedule match identity insert conflict');
END;

CREATE TRIGGER trg_schedule_identity_no_update
BEFORE UPDATE ON schedule_match_identity
BEGIN
  SELECT RAISE(ABORT, 'schedule match identity is immutable');
END;

CREATE TRIGGER trg_schedule_identity_no_delete
BEFORE DELETE ON schedule_match_identity
BEGIN
  SELECT RAISE(ABORT, 'schedule match identity cannot be deleted');
END;

CREATE TABLE schedule_match_state_snapshot (
  id                   INTEGER PRIMARY KEY,
  match_identity_id    INTEGER NOT NULL REFERENCES schedule_match_identity(id),
  state_content_hash   TEXT NOT NULL
                       CHECK (
                         length(state_content_hash) = 64
                         AND state_content_hash NOT GLOB '*[^0-9a-f]*'
                       ),
  kickoff_at_utc       TEXT
                       CHECK (
                         kickoff_at_utc IS NULL
                         OR COALESCE(
                           length(kickoff_at_utc) = 27
                           AND kickoff_at_utc GLOB
                             '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                           AND CAST(substr(kickoff_at_utc, 1, 4) AS INTEGER)
                             BETWEEN 1 AND 9999
                           AND CAST(substr(kickoff_at_utc, 12, 2) AS INTEGER)
                             BETWEEN 0 AND 23
                           AND CAST(substr(kickoff_at_utc, 15, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND CAST(substr(kickoff_at_utc, 18, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND date(substr(kickoff_at_utc, 1, 10), '+0 days')
                             = substr(kickoff_at_utc, 1, 10),
                           0
                         )
                       ),
  kickoff_precision    TEXT NOT NULL
                       CHECK (kickoff_precision IN ('exact','date_only','unknown')),
  status               TEXT NOT NULL CHECK (length(trim(status)) > 0),
  finished             INTEGER NOT NULL CHECK (finished IN (0,1)),
  cancelled            INTEGER NOT NULL CHECK (cancelled IN (0,1)),
  home_team_id         INTEGER,
  home_team_name       TEXT,
  away_team_id         INTEGER,
  away_team_name       TEXT,
  competition_id       TEXT NOT NULL CHECK (length(trim(competition_id)) > 0),
  season_label         TEXT NOT NULL CHECK (length(trim(season_label)) > 0),
  round_label          TEXT,
  stage_label          TEXT,
  competition_class    TEXT NOT NULL CHECK (length(trim(competition_class)) > 0),
  competition_verified INTEGER NOT NULL CHECK (competition_verified IN (0,1)),
  source_updated_at    TEXT
                       CHECK (
                         source_updated_at IS NULL
                         OR COALESCE(
                           length(source_updated_at) = 27
                           AND source_updated_at GLOB
                             '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                           AND CAST(substr(source_updated_at, 1, 4) AS INTEGER)
                             BETWEEN 1 AND 9999
                           AND CAST(substr(source_updated_at, 12, 2) AS INTEGER)
                             BETWEEN 0 AND 23
                           AND CAST(substr(source_updated_at, 15, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND CAST(substr(source_updated_at, 18, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND date(substr(source_updated_at, 1, 10), '+0 days')
                             = substr(source_updated_at, 1, 10),
                           0
                         )
                       ),
  first_observed_at    TEXT NOT NULL
                       CHECK (
                         COALESCE(
                           length(first_observed_at) = 27
                           AND first_observed_at GLOB
                             '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                           AND CAST(substr(first_observed_at, 1, 4) AS INTEGER)
                             BETWEEN 1 AND 9999
                           AND CAST(substr(first_observed_at, 12, 2) AS INTEGER)
                             BETWEEN 0 AND 23
                           AND CAST(substr(first_observed_at, 15, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND CAST(substr(first_observed_at, 18, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND date(substr(first_observed_at, 1, 10), '+0 days')
                             = substr(first_observed_at, 1, 10),
                           0
                         )
                       ),
  ingested_at          TEXT NOT NULL
                       CHECK (
                         COALESCE(
                           length(ingested_at) = 27
                           AND ingested_at GLOB
                             '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                           AND CAST(substr(ingested_at, 1, 4) AS INTEGER)
                             BETWEEN 1 AND 9999
                           AND CAST(substr(ingested_at, 12, 2) AS INTEGER)
                             BETWEEN 0 AND 23
                           AND CAST(substr(ingested_at, 15, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND CAST(substr(ingested_at, 18, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND date(substr(ingested_at, 1, 10), '+0 days')
                             = substr(ingested_at, 1, 10),
                           0
                         )
                       ),
  provenance           TEXT NOT NULL CHECK (length(trim(provenance)) > 0),
  CHECK (NOT (finished = 1 AND cancelled = 1)),
  CHECK (home_team_id IS NULL OR home_team_id > 0),
  CHECK (away_team_id IS NULL OR away_team_id > 0),
  CHECK (
    home_team_id IS NULL
    OR away_team_id IS NULL
    OR home_team_id <> away_team_id
  ),
  CHECK (kickoff_precision <> 'exact' OR kickoff_at_utc IS NOT NULL),
  UNIQUE (match_identity_id, state_content_hash)
);

CREATE INDEX idx_schedule_state_identity
  ON schedule_match_state_snapshot(match_identity_id, id);

CREATE INDEX idx_schedule_state_scope_kickoff
  ON schedule_match_state_snapshot(
    competition_id,
    season_label,
    kickoff_at_utc,
    match_identity_id
  );

CREATE TRIGGER trg_schedule_state_insert_guard
BEFORE INSERT ON schedule_match_state_snapshot
WHEN
  NOT EXISTS (
    SELECT 1 FROM schedule_match_identity
    WHERE id = NEW.match_identity_id
  )
  OR EXISTS (
    SELECT 1 FROM schedule_match_state_snapshot
    WHERE id = NEW.id
  )
  OR EXISTS (
    SELECT 1 FROM schedule_match_state_snapshot
    WHERE match_identity_id = NEW.match_identity_id
      AND state_content_hash = NEW.state_content_hash
  )
BEGIN
  SELECT RAISE(ABORT, 'schedule match state insert conflict');
END;

CREATE TRIGGER trg_schedule_state_no_update
BEFORE UPDATE ON schedule_match_state_snapshot
BEGIN
  SELECT RAISE(ABORT, 'schedule match state snapshot is append only');
END;

CREATE TRIGGER trg_schedule_state_no_delete
BEFORE DELETE ON schedule_match_state_snapshot
BEGIN
  SELECT RAISE(ABORT, 'schedule match state snapshot cannot be deleted');
END;

-- One observation event may be associated with multiple match snapshots.
CREATE TABLE schedule_observation_event (
  id                 INTEGER PRIMARY KEY,
  provider           TEXT NOT NULL CHECK (length(trim(provider)) > 0),
  source             TEXT NOT NULL CHECK (length(trim(source)) > 0),
  competition_scope  TEXT NOT NULL CHECK (length(trim(competition_scope)) > 0),
  season_scope       TEXT NOT NULL CHECK (length(trim(season_scope)) > 0),
  observed_at        TEXT NOT NULL
                     CHECK (
                       COALESCE(
                         length(observed_at) = 27
                         AND observed_at GLOB
                           '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                         AND CAST(substr(observed_at, 1, 4) AS INTEGER)
                           BETWEEN 1 AND 9999
                         AND CAST(substr(observed_at, 12, 2) AS INTEGER)
                           BETWEEN 0 AND 23
                         AND CAST(substr(observed_at, 15, 2) AS INTEGER)
                           BETWEEN 0 AND 59
                         AND CAST(substr(observed_at, 18, 2) AS INTEGER)
                           BETWEEN 0 AND 59
                         AND date(substr(observed_at, 1, 10), '+0 days')
                           = substr(observed_at, 1, 10),
                         0
                       )
                     ),
  poll_run_id        TEXT CHECK (
                       poll_run_id IS NULL OR length(trim(poll_run_id)) > 0
                     ),
  payload_hash       TEXT NOT NULL
                     CHECK (
                       length(payload_hash) = 64
                       AND payload_hash NOT GLOB '*[^0-9a-f]*'
                     ),
  ingested_at        TEXT NOT NULL
                     CHECK (
                       COALESCE(
                         length(ingested_at) = 27
                         AND ingested_at GLOB
                           '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                         AND CAST(substr(ingested_at, 1, 4) AS INTEGER)
                           BETWEEN 1 AND 9999
                         AND CAST(substr(ingested_at, 12, 2) AS INTEGER)
                           BETWEEN 0 AND 23
                         AND CAST(substr(ingested_at, 15, 2) AS INTEGER)
                           BETWEEN 0 AND 59
                         AND CAST(substr(ingested_at, 18, 2) AS INTEGER)
                           BETWEEN 0 AND 59
                         AND date(substr(ingested_at, 1, 10), '+0 days')
                           = substr(ingested_at, 1, 10),
                         0
                       )
                     )
);

CREATE UNIQUE INDEX uq_schedule_observation_event_natural
  ON schedule_observation_event(
    provider,
    source,
    competition_scope,
    season_scope,
    observed_at,
    COALESCE(poll_run_id, ''),
    payload_hash
  );

CREATE TRIGGER trg_schedule_observation_event_insert_guard
BEFORE INSERT ON schedule_observation_event
WHEN
  EXISTS (
    SELECT 1 FROM schedule_observation_event
    WHERE id = NEW.id
  )
  OR EXISTS (
    SELECT 1 FROM schedule_observation_event
    WHERE provider = NEW.provider
      AND source = NEW.source
      AND competition_scope = NEW.competition_scope
      AND season_scope = NEW.season_scope
      AND observed_at = NEW.observed_at
      AND COALESCE(poll_run_id, '') = COALESCE(NEW.poll_run_id, '')
      AND payload_hash = NEW.payload_hash
  )
BEGIN
  SELECT RAISE(ABORT, 'schedule observation event insert conflict');
END;

CREATE TRIGGER trg_schedule_observation_event_no_update
BEFORE UPDATE ON schedule_observation_event
BEGIN
  SELECT RAISE(ABORT, 'schedule observation event is append only');
END;

CREATE TRIGGER trg_schedule_observation_event_no_delete
BEFORE DELETE ON schedule_observation_event
BEGIN
  SELECT RAISE(ABORT, 'schedule observation event cannot be deleted');
END;

CREATE TABLE schedule_match_observation (
  id                   INTEGER PRIMARY KEY,
  observation_event_id INTEGER NOT NULL REFERENCES schedule_observation_event(id),
  match_identity_id    INTEGER NOT NULL REFERENCES schedule_match_identity(id),
  snapshot_id          INTEGER NOT NULL REFERENCES schedule_match_state_snapshot(id),
  observed_at          TEXT NOT NULL
                       CHECK (
                         COALESCE(
                           length(observed_at) = 27
                           AND observed_at GLOB
                             '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                           AND CAST(substr(observed_at, 1, 4) AS INTEGER)
                             BETWEEN 1 AND 9999
                           AND CAST(substr(observed_at, 12, 2) AS INTEGER)
                             BETWEEN 0 AND 23
                           AND CAST(substr(observed_at, 15, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND CAST(substr(observed_at, 18, 2) AS INTEGER)
                             BETWEEN 0 AND 59
                           AND date(substr(observed_at, 1, 10), '+0 days')
                             = substr(observed_at, 1, 10),
                           0
                         )
                       ),
  UNIQUE (match_identity_id, observed_at),
  UNIQUE (observation_event_id, match_identity_id)
);

CREATE INDEX idx_schedule_observation_current
  ON schedule_match_observation(match_identity_id, observed_at DESC, id DESC);

CREATE INDEX idx_schedule_observation_snapshot
  ON schedule_match_observation(snapshot_id, observed_at);

CREATE TRIGGER trg_schedule_observation_insert_guard
BEFORE INSERT ON schedule_match_observation
WHEN
  NOT EXISTS (
    SELECT 1 FROM schedule_observation_event
    WHERE id = NEW.observation_event_id
  )
  OR NOT EXISTS (
    SELECT 1 FROM schedule_match_identity
    WHERE id = NEW.match_identity_id
  )
  OR NOT EXISTS (
    SELECT 1 FROM schedule_match_state_snapshot
    WHERE id = NEW.snapshot_id
      AND match_identity_id = NEW.match_identity_id
  )
  OR NOT EXISTS (
    SELECT 1
    FROM schedule_observation_event AS event
    JOIN schedule_match_identity AS identity
      ON identity.id = NEW.match_identity_id
    WHERE event.id = NEW.observation_event_id
      AND event.provider = identity.provider
      AND event.observed_at = NEW.observed_at
  )
  OR EXISTS (
    SELECT 1 FROM schedule_match_observation
    WHERE id = NEW.id
       OR (
         match_identity_id = NEW.match_identity_id
         AND observed_at = NEW.observed_at
       )
       OR (
         observation_event_id = NEW.observation_event_id
         AND match_identity_id = NEW.match_identity_id
       )
  )
BEGIN
  SELECT RAISE(ABORT, 'schedule observation association conflict');
END;

CREATE TRIGGER trg_schedule_observation_no_update
BEFORE UPDATE ON schedule_match_observation
BEGIN
  SELECT RAISE(ABORT, 'schedule observation association is append only');
END;

CREATE TRIGGER trg_schedule_observation_no_delete
BEFORE DELETE ON schedule_match_observation
BEGIN
  SELECT RAISE(ABORT, 'schedule observation association cannot be deleted');
END;

CREATE VIEW current_schedule_match_state AS
SELECT
  ranked.match_identity_id,
  ranked.provider,
  ranked.provider_match_id,
  ranked.canonical_match_id,
  ranked.snapshot_id,
  ranked.state_content_hash,
  ranked.kickoff_at_utc,
  ranked.kickoff_precision,
  ranked.status,
  ranked.finished,
  ranked.cancelled,
  ranked.home_team_id,
  ranked.home_team_name,
  ranked.away_team_id,
  ranked.away_team_name,
  ranked.competition_id,
  ranked.season_label,
  ranked.round_label,
  ranked.stage_label,
  ranked.competition_class,
  ranked.competition_verified,
  ranked.source_updated_at,
  ranked.first_observed_at,
  ranked.snapshot_ingested_at,
  ranked.provenance,
  ranked.observation_id,
  ranked.observation_event_id,
  ranked.observed_at,
  ranked.poll_run_id,
  ranked.payload_hash,
  ranked.observation_ingested_at
FROM (
  SELECT
    identity.id AS match_identity_id,
    identity.provider,
    identity.provider_match_id,
    identity.canonical_match_id,
    snapshot.id AS snapshot_id,
    snapshot.state_content_hash,
    snapshot.kickoff_at_utc,
    snapshot.kickoff_precision,
    snapshot.status,
    snapshot.finished,
    snapshot.cancelled,
    snapshot.home_team_id,
    snapshot.home_team_name,
    snapshot.away_team_id,
    snapshot.away_team_name,
    snapshot.competition_id,
    snapshot.season_label,
    snapshot.round_label,
    snapshot.stage_label,
    snapshot.competition_class,
    snapshot.competition_verified,
    snapshot.source_updated_at,
    snapshot.first_observed_at,
    snapshot.ingested_at AS snapshot_ingested_at,
    snapshot.provenance,
    association.id AS observation_id,
    event.id AS observation_event_id,
    association.observed_at,
    event.poll_run_id,
    event.payload_hash,
    event.ingested_at AS observation_ingested_at,
    ROW_NUMBER() OVER (
      PARTITION BY identity.id
      ORDER BY association.observed_at DESC, association.id DESC
    ) AS projection_rank
  FROM schedule_match_identity AS identity
  JOIN schedule_match_observation AS association
    ON association.match_identity_id = identity.id
  JOIN schedule_observation_event AS event
    ON event.id = association.observation_event_id
  JOIN schedule_match_state_snapshot AS snapshot
    ON snapshot.id = association.snapshot_id
) AS ranked
WHERE ranked.projection_rank = 1;

-- Build phase: immutable lineage header followed by ordered inputs.
CREATE TABLE schedule_rest_lineage_set (
  id                       INTEGER PRIMARY KEY,
  team_id                  INTEGER NOT NULL CHECK (team_id > 0),
  target_match_identity_id INTEGER NOT NULL REFERENCES schedule_match_identity(id),
  target_snapshot_id       INTEGER NOT NULL REFERENCES schedule_match_state_snapshot(id),
  feature_definition       TEXT NOT NULL CHECK (length(trim(feature_definition)) > 0),
  feature_version          TEXT NOT NULL CHECK (length(trim(feature_version)) > 0),
  as_of_observed_at        TEXT NOT NULL
                            CHECK (
                              COALESCE(
                                length(as_of_observed_at) = 27
                                AND as_of_observed_at GLOB
                                  '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                                AND CAST(substr(as_of_observed_at, 1, 4) AS INTEGER)
                                  BETWEEN 1 AND 9999
                                AND CAST(substr(as_of_observed_at, 12, 2) AS INTEGER)
                                  BETWEEN 0 AND 23
                                AND CAST(substr(as_of_observed_at, 15, 2) AS INTEGER)
                                  BETWEEN 0 AND 59
                                AND CAST(substr(as_of_observed_at, 18, 2) AS INTEGER)
                                  BETWEEN 0 AND 59
                                AND date(substr(as_of_observed_at, 1, 10), '+0 days')
                                  = substr(as_of_observed_at, 1, 10),
                                0
                              )
                            ),
  input_set_hash           TEXT NOT NULL
                            CHECK (
                              length(input_set_hash) = 64
                              AND input_set_hash NOT GLOB '*[^0-9a-f]*'
                            ),
  expected_input_count     INTEGER NOT NULL CHECK (expected_input_count > 0),
  UNIQUE (
    team_id,
    target_match_identity_id,
    target_snapshot_id,
    feature_definition,
    feature_version,
    input_set_hash
  )
);

CREATE INDEX idx_schedule_rest_lineage_target
  ON schedule_rest_lineage_set(
    target_match_identity_id,
    team_id,
    feature_definition,
    feature_version,
    as_of_observed_at
  );

CREATE TRIGGER trg_schedule_rest_lineage_set_insert_guard
BEFORE INSERT ON schedule_rest_lineage_set
WHEN
  NOT EXISTS (
    SELECT 1 FROM schedule_match_identity
    WHERE id = NEW.target_match_identity_id
  )
  OR NOT EXISTS (
    SELECT 1 FROM schedule_match_state_snapshot
    WHERE id = NEW.target_snapshot_id
      AND match_identity_id = NEW.target_match_identity_id
      AND (home_team_id = NEW.team_id OR away_team_id = NEW.team_id)
  )
  OR EXISTS (
    SELECT 1 FROM schedule_rest_lineage_set
    WHERE id = NEW.id
       OR (
         team_id = NEW.team_id
         AND target_match_identity_id = NEW.target_match_identity_id
         AND target_snapshot_id = NEW.target_snapshot_id
         AND feature_definition = NEW.feature_definition
         AND feature_version = NEW.feature_version
         AND input_set_hash = NEW.input_set_hash
       )
  )
BEGIN
  SELECT RAISE(ABORT, 'schedule rest lineage set conflict');
END;

CREATE TRIGGER trg_schedule_rest_lineage_set_no_update
BEFORE UPDATE ON schedule_rest_lineage_set
BEGIN
  SELECT RAISE(ABORT, 'schedule rest lineage set is append only');
END;

CREATE TRIGGER trg_schedule_rest_lineage_set_no_delete
BEFORE DELETE ON schedule_rest_lineage_set
BEGIN
  SELECT RAISE(ABORT, 'schedule rest lineage set cannot be deleted');
END;

CREATE TABLE schedule_rest_lineage_input (
  lineage_set_id          INTEGER NOT NULL REFERENCES schedule_rest_lineage_set(id),
  input_ordinal           INTEGER NOT NULL CHECK (input_ordinal >= 0),
  input_match_identity_id INTEGER NOT NULL REFERENCES schedule_match_identity(id),
  input_snapshot_id       INTEGER NOT NULL REFERENCES schedule_match_state_snapshot(id),
  PRIMARY KEY (lineage_set_id, input_ordinal),
  UNIQUE (lineage_set_id, input_match_identity_id),
  UNIQUE (lineage_set_id, input_snapshot_id)
);

CREATE INDEX idx_schedule_rest_lineage_input_snapshot
  ON schedule_rest_lineage_input(input_snapshot_id, lineage_set_id);

CREATE TRIGGER trg_schedule_rest_lineage_input_guard
BEFORE INSERT ON schedule_rest_lineage_input
WHEN
  NOT EXISTS (
    SELECT 1 FROM schedule_rest_lineage_set
    WHERE id = NEW.lineage_set_id
  )
  OR NOT EXISTS (
    SELECT 1 FROM schedule_match_identity
    WHERE id = NEW.input_match_identity_id
  )
  OR NOT EXISTS (
    SELECT 1 FROM schedule_match_state_snapshot
    WHERE id = NEW.input_snapshot_id
      AND match_identity_id = NEW.input_match_identity_id
  )
  OR EXISTS (
    SELECT 1 FROM schedule_rest_lineage_input
    WHERE (
      lineage_set_id = NEW.lineage_set_id
      AND input_ordinal = NEW.input_ordinal
    )
    OR (
      lineage_set_id = NEW.lineage_set_id
      AND input_match_identity_id = NEW.input_match_identity_id
    )
    OR (
      lineage_set_id = NEW.lineage_set_id
      AND input_snapshot_id = NEW.input_snapshot_id
    )
  )
  OR NEW.input_ordinal <> (
    SELECT COUNT(*) FROM schedule_rest_lineage_input
    WHERE lineage_set_id = NEW.lineage_set_id
  )
  OR NEW.input_ordinal >= (
    SELECT expected_input_count FROM schedule_rest_lineage_set
    WHERE id = NEW.lineage_set_id
  )
  OR NOT EXISTS (
    SELECT 1
    FROM schedule_rest_lineage_set AS lineage
    JOIN schedule_match_state_snapshot AS input_snapshot
      ON input_snapshot.id = NEW.input_snapshot_id
    WHERE lineage.id = NEW.lineage_set_id
      AND input_snapshot.kickoff_precision = 'exact'
      AND input_snapshot.kickoff_at_utc IS NOT NULL
      AND (
        input_snapshot.home_team_id = lineage.team_id
        OR input_snapshot.away_team_id = lineage.team_id
      )
      AND input_snapshot.kickoff_at_utc <= (
        SELECT kickoff_at_utc
        FROM schedule_match_state_snapshot
        WHERE id = lineage.target_snapshot_id
      )
      AND EXISTS (
        SELECT 1
        FROM schedule_match_observation AS association
        WHERE association.match_identity_id = NEW.input_match_identity_id
          AND association.snapshot_id = NEW.input_snapshot_id
          AND association.observed_at <= lineage.as_of_observed_at
      )
  )
  OR (
    NEW.input_ordinal > 0
    AND (
      SELECT kickoff_at_utc FROM schedule_match_state_snapshot
      WHERE id = NEW.input_snapshot_id
    ) <= (
      SELECT previous_snapshot.kickoff_at_utc
      FROM schedule_rest_lineage_input AS previous_input
      JOIN schedule_match_state_snapshot AS previous_snapshot
        ON previous_snapshot.id = previous_input.input_snapshot_id
      WHERE previous_input.lineage_set_id = NEW.lineage_set_id
        AND previous_input.input_ordinal = NEW.input_ordinal - 1
    )
  )
  OR (
    NEW.input_ordinal = (
      SELECT expected_input_count - 1
      FROM schedule_rest_lineage_set
      WHERE id = NEW.lineage_set_id
    )
    AND NEW.input_snapshot_id <> (
      SELECT target_snapshot_id
      FROM schedule_rest_lineage_set
      WHERE id = NEW.lineage_set_id
    )
  )
BEGIN
  SELECT RAISE(ABORT, 'schedule rest lineage input contract violation');
END;

CREATE TRIGGER trg_schedule_rest_lineage_input_no_update
BEFORE UPDATE ON schedule_rest_lineage_input
BEGIN
  SELECT RAISE(ABORT, 'schedule rest lineage input is append only');
END;

CREATE TRIGGER trg_schedule_rest_lineage_input_no_delete
BEFORE DELETE ON schedule_rest_lineage_input
BEGIN
  SELECT RAISE(ABORT, 'schedule rest lineage input cannot be deleted');
END;

-- Finalization phase: this is the only consumable feature table.
CREATE TABLE schedule_rest_feature (
  id                       INTEGER PRIMARY KEY,
  lineage_set_id           INTEGER NOT NULL UNIQUE
                           REFERENCES schedule_rest_lineage_set(id),
  team_id                  INTEGER NOT NULL CHECK (team_id > 0),
  target_match_identity_id INTEGER NOT NULL REFERENCES schedule_match_identity(id),
  target_snapshot_id       INTEGER NOT NULL REFERENCES schedule_match_state_snapshot(id),
  feature_definition       TEXT NOT NULL CHECK (length(trim(feature_definition)) > 0),
  feature_version          TEXT NOT NULL CHECK (length(trim(feature_version)) > 0),
  as_of_observed_at        TEXT NOT NULL
                            CHECK (
                              COALESCE(
                                length(as_of_observed_at) = 27
                                AND as_of_observed_at GLOB
                                  '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                                AND CAST(substr(as_of_observed_at, 1, 4) AS INTEGER)
                                  BETWEEN 1 AND 9999
                                AND CAST(substr(as_of_observed_at, 12, 2) AS INTEGER)
                                  BETWEEN 0 AND 23
                                AND CAST(substr(as_of_observed_at, 15, 2) AS INTEGER)
                                  BETWEEN 0 AND 59
                                AND CAST(substr(as_of_observed_at, 18, 2) AS INTEGER)
                                  BETWEEN 0 AND 59
                                AND date(substr(as_of_observed_at, 1, 10), '+0 days')
                                  = substr(as_of_observed_at, 1, 10),
                                0
                              )
                            ),
  input_set_hash           TEXT NOT NULL
                            CHECK (
                              length(input_set_hash) = 64
                              AND input_set_hash NOT GLOB '*[^0-9a-f]*'
                            ),
  input_count              INTEGER NOT NULL CHECK (input_count > 0),
  feature_payload_hash     TEXT NOT NULL
                            CHECK (
                              length(feature_payload_hash) = 64
                              AND feature_payload_hash NOT GLOB '*[^0-9a-f]*'
                            ),
  feature_value_json       TEXT NOT NULL CHECK (json_valid(feature_value_json)),
  computation_status       TEXT NOT NULL
                            CHECK (computation_status IN ('computed','excluded','failed')),
  provenance               TEXT NOT NULL CHECK (length(trim(provenance)) > 0),
  computed_at              TEXT NOT NULL
                            CHECK (
                              COALESCE(
                                length(computed_at) = 27
                                AND computed_at GLOB
                                  '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z'
                                AND CAST(substr(computed_at, 1, 4) AS INTEGER)
                                  BETWEEN 1 AND 9999
                                AND CAST(substr(computed_at, 12, 2) AS INTEGER)
                                  BETWEEN 0 AND 23
                                AND CAST(substr(computed_at, 15, 2) AS INTEGER)
                                  BETWEEN 0 AND 59
                                AND CAST(substr(computed_at, 18, 2) AS INTEGER)
                                  BETWEEN 0 AND 59
                                AND date(substr(computed_at, 1, 10), '+0 days')
                                  = substr(computed_at, 1, 10),
                                0
                              )
                            ),
  UNIQUE (
    team_id,
    target_match_identity_id,
    target_snapshot_id,
    feature_definition,
    feature_version,
    input_set_hash
  )
);

CREATE INDEX idx_schedule_rest_feature_target
  ON schedule_rest_feature(
    target_match_identity_id,
    team_id,
    feature_definition,
    feature_version,
    as_of_observed_at
  );

CREATE TRIGGER trg_schedule_rest_feature_finalize_guard
BEFORE INSERT ON schedule_rest_feature
WHEN
  NOT EXISTS (
    SELECT 1 FROM schedule_rest_lineage_set
    WHERE id = NEW.lineage_set_id
  )
  OR NOT EXISTS (
    SELECT 1
    FROM schedule_rest_lineage_set AS lineage
    WHERE lineage.id = NEW.lineage_set_id
      AND lineage.team_id = NEW.team_id
      AND lineage.target_match_identity_id = NEW.target_match_identity_id
      AND lineage.target_snapshot_id = NEW.target_snapshot_id
      AND lineage.feature_definition = NEW.feature_definition
      AND lineage.feature_version = NEW.feature_version
      AND lineage.as_of_observed_at = NEW.as_of_observed_at
      AND lineage.input_set_hash = NEW.input_set_hash
      AND lineage.expected_input_count = NEW.input_count
      AND (
        SELECT COUNT(*) FROM schedule_rest_lineage_input
        WHERE lineage_set_id = lineage.id
      ) = lineage.expected_input_count
      AND (
        SELECT MIN(input_ordinal) FROM schedule_rest_lineage_input
        WHERE lineage_set_id = lineage.id
      ) = 0
      AND (
        SELECT MAX(input_ordinal) FROM schedule_rest_lineage_input
        WHERE lineage_set_id = lineage.id
      ) = lineage.expected_input_count - 1
      AND EXISTS (
        SELECT 1 FROM schedule_rest_lineage_input
        WHERE lineage_set_id = lineage.id
          AND input_ordinal = lineage.expected_input_count - 1
          AND input_match_identity_id = lineage.target_match_identity_id
          AND input_snapshot_id = lineage.target_snapshot_id
      )
  )
  OR EXISTS (
    SELECT 1 FROM schedule_rest_feature
    WHERE id = NEW.id
       OR lineage_set_id = NEW.lineage_set_id
       OR (
         team_id = NEW.team_id
         AND target_match_identity_id = NEW.target_match_identity_id
         AND target_snapshot_id = NEW.target_snapshot_id
         AND feature_definition = NEW.feature_definition
         AND feature_version = NEW.feature_version
         AND input_set_hash = NEW.input_set_hash
       )
  )
BEGIN
  SELECT RAISE(ABORT, 'schedule rest feature finalization conflict');
END;

CREATE TRIGGER trg_schedule_rest_feature_no_update
BEFORE UPDATE ON schedule_rest_feature
BEGIN
  SELECT RAISE(ABORT, 'schedule rest feature is append only');
END;

CREATE TRIGGER trg_schedule_rest_feature_no_delete
BEFORE DELETE ON schedule_rest_feature
BEGIN
  SELECT RAISE(ABORT, 'schedule rest feature cannot be deleted');
END;

-- Read-only compatibility projection: only finalized lineage is visible.
CREATE VIEW schedule_rest_feature_input AS
SELECT
  feature.id AS feature_id,
  input.input_ordinal,
  input.input_match_identity_id,
  input.input_snapshot_id
FROM schedule_rest_feature AS feature
JOIN schedule_rest_lineage_input AS input
  ON input.lineage_set_id = feature.lineage_set_id;

CREATE TRIGGER trg_schedule_rest_feature_input_no_insert
INSTEAD OF INSERT ON schedule_rest_feature_input
BEGIN
  SELECT RAISE(ABORT, 'finalized feature input view is read only');
END;

CREATE TRIGGER trg_schedule_rest_feature_input_no_update
INSTEAD OF UPDATE ON schedule_rest_feature_input
BEGIN
  SELECT RAISE(ABORT, 'finalized feature input view is read only');
END;

CREATE TRIGGER trg_schedule_rest_feature_input_no_delete
INSTEAD OF DELETE ON schedule_rest_feature_input
BEGIN
  SELECT RAISE(ABORT, 'finalized feature input view is read only');
END;
