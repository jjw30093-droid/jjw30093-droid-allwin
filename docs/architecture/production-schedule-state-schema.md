# Production schedule-state schema v1

## Status and boundary

`PRODUCTION_SCHEDULE_STATE_SCHEMA_V1` is a formal core-database schema whose
migration, state transitions, projections, and lineage behavior are validated
only against newly created temporary SQLite databases.

The prototype phase is closed. The CWC fixture is a validated offline semantic
reference, not authority to use a production data source. Production
ingestion, Worker registration, systemd, API, frontend, and migration of real
data are **NOT STARTED**. The permanently sealed CWC live/resume runner is not
part of this design and must not be revived.

## Database placement

The schema belongs in `core` / `data/allwin.db`, for these repository-backed
reasons:

- the existing canonical FotMob `Match_ID` and legacy `dim_match` live in core;
- existing model input and feature products (`int_match_features`) live in
  core;
- `odds.db` owns cross-provider `dim_match_xref` and polling throttle
  `poll_state`, not canonical schedule history;
- SQLite cannot enforce foreign keys across the core and odds files.

Migration `core/0003_schedule_state_v1.sql` adds new objects only. It does not
alter, update, rename, rebuild, backfill, or change the behavior of
`dim_match`. Existing readers and writers continue to use that legacy current
table until a separately authorized integration phase.

## Data roles

All timestamps that participate in ordering, uniqueness, current/as-of
selection, or evidence are persisted in one fixed-width UTC representation:
`YYYY-MM-DDTHH:MM:SS.ffffffZ`. Python accepts `Z` or an explicit zero offset
and canonicalizes it. Naive/non-UTC input is rejected. Database CHECK
constraints independently enforce length, separators, digit positions,
calendar date round-trip, time ranges, and the terminal `Z`; direct SQL cannot
store an alternative spelling.

### Stable identity

`schedule_match_identity` owns an internal integer ID and the immutable natural
key `(provider, provider_match_id)`. `canonical_match_id` is nullable and,
when present, references the existing `dim_match.Match_ID`. A partial unique
index prevents one provider from binding two provider IDs to the same
canonical match.

The service canonicalizes provider input with NFKC, trim, and lowercase, then
requires an ASCII slug beginning with `a-z`, followed only by
`a-z0-9_-`, with a maximum length of 32. Provider match IDs accept only a
positive non-bool integer or a supported ASCII string beginning with an
alphanumeric and containing only `A-Z a-z 0-9 . _ : -`, with a maximum
length of 128. Numeric strings are positive and canonicalized without leading
zeroes. Lists, mappings, floats, booleans, null, paths, whitespace/control
characters, and unsupported Unicode are rejected rather than stringified.
Migration `0003` independently enforces the canonical direct-SQL subset.

The identity row stores creation time and identity provenance. `created_at`
is the time that immutable row first committed: it is first-write provenance,
not part of replay equality. Replaying the same stable identity at a later or
earlier run time skips without updating the original timestamp. It stores no
kickoff, status, teams, competition assignment, round, stage, or display
state. UPDATE and DELETE are rejected by triggers. An initially null or
different canonical binding is not silently rewritten; a future explicit
reconciliation workflow requires separate authorization and schema semantics.

`odds.db.dim_match_xref` remains the cross-provider resolution record. This
core identity table neither replaces nor mutates it.

### Append-only match state

`schedule_match_state_snapshot` stores one immutable business-state version:

- kickoff and precision;
- status, finished, and cancelled;
- home/away IDs and display names, including nullable TBD identities;
- competition, season, round, stage, class, and verification;
- nullable source-declared update time;
- first observation time, ingestion time, and provenance.

`state_content_hash` is SHA-256 over business-state fields only. Observation
time, ingestion time, source update time, database path, host, and wall clock
are excluded. `(match_identity_id, state_content_hash)` therefore makes a
repeated business state reuse its original snapshot. NS→FT, postponement,
reschedule, cancellation, cancelled→scheduled recovery, TBD correction, and
round/stage correction create a new snapshot when their business content is
new. Returning to a previously seen state reuses the old snapshot and adds a
new observation; it never overwrites history.

UPDATE and DELETE are rejected by database triggers.

### Observation event and match association

`schedule_observation_event` records provider/source, competition and season
scope, event-time `observed_at`, optional `poll_run_id`, source payload hash,
and ingestion time. `schedule_match_observation` associates that immutable
event with one stable match identity and one state snapshot. One poll/response
event can therefore associate multiple matches without making
`poll_run_id` globally unique.

The per-match association key is `(match_identity_id, observed_at)`:

- same time and identical evidence is an idempotent skip;
- later or earlier observation of an existing state appends only a ledger row;
- earlier late arrival cannot regress current state;
- same event time with different state/evidence is a conflict and rolls back
  the complete transaction.

Triggers use positive `NOT EXISTS` checks to verify event, provider, identity,
snapshot, and event time even when a raw connection has disabled foreign
keys. UPDATE and DELETE are rejected on both layers. The ledger is observation
evidence, not a substitute for the per-match state snapshot.

## Current and as-of semantics

`current_schedule_match_state` is an executable SQLite view. For each stable
identity it selects the observation with:

1. greatest event-time `observed_at`;
2. greatest observation ID as a declared stable final tie-breaker.

Same-time business conflicts are rejected before insertion, so the second
term is defensive and deterministic rather than last-write-wins policy.
Ingestion time and source update time do not select current state.

`backend.schedules.state.get_match_state_as_of()` applies the same ordering
after restricting observations to `observed_at <= :as_of`. The snapshot table
remains historical truth; the view and query are projections.

## Versioned rest-feature lineage and finalization

Feature persistence deliberately separates build state from consumable state:

- `schedule_rest_lineage_set` is the immutable header: team, target
  identity/snapshot, definition/version, as-of time, input hash, and expected
  input count;
- `schedule_rest_lineage_input` appends the exact ordered identity/snapshot
  inputs;
- `schedule_rest_feature` is inserted only after a finalization trigger proves
  the lineage complete. It is the only consumable feature table.

The read-only `schedule_rest_feature_input` compatibility view joins finalized
features to lineage inputs; an incomplete build cannot appear in that view.
Database constraints and triggers enforce:

- contiguous zero-based input order;
- unique input match identity and snapshot per feature;
- snapshot/identity agreement;
- the team participates in every input match;
- exact, strictly increasing kickoff times;
- no input kickoff later than the target;
- the final input snapshot is the target snapshot;
- append-only header, input, and finalized feature history;
- actual input count equals expected input count before finalization;
- zero-based ordinals are contiguous and the final input is the target.

The input hash includes ordered match identity, snapshot identity, snapshot
content hash, kickoff, and team identities. It excludes as-of/computation wall
clock, temporary database path, machine, and process state. Repeating the same
input is idempotent. A changed earlier state creates a new lineage version for
that match and downstream targets only; prior features remain queryable.
Cancelled, unfinished, imprecise, unverified, and non-competitive current
states are excluded from observed-historical rest features.

## Transaction and conflict policy

Identity, state snapshot, observation event, and match association are written in one
`BEGIN IMMEDIATE` transaction. A conflicting immutable identity, same-time
observation, foreign key, CHECK, trigger, or injected failure rolls back all
new rows.

Lineage header, ordered inputs, and final feature are likewise written in one
transaction. The command verifies that every input snapshot was observable at
or before the feature as-of time. Normal idempotency performs an exact lookup
before insertion. Every append-only table also has a BEFORE INSERT conflict
guard for both primary and natural keys, so `INSERT OR REPLACE` cannot exploit
SQLite's implicit delete path even when `recursive_triggers=OFF`. UPSERT
updates, ordinary UPDATE/DELETE, silent sorting/deduplication, and
last-write-wins are rejected.

## Migration, validation, and rollback

Before opening a writable database, the migration runner validates a non-empty
manifest whose numeric versions are continuous from 1. Migration identity is
the exact triple `(version, filename, checksum)`: an applied version missing
from the manifest, filename mismatch, duplicate version, gap, or checksum
drift fails closed. The runner then supplies one transaction per migration and
idempotent reruns. The v1 wrapper additionally compares every reviewed table,
index, trigger, and view in `sqlite_master` with the migration DDL and rejects
partial, drifted, weakened, extra, or wrong-type objects.

Offline proof covers:

- fresh core database;
- legacy core database upgraded without changing `dim_match`;
- second migration run;
- partial schema;
- wrong same-name object;
- post-application drift and weakened constraint text;
- mid-migration failure;
- missing-first/missing-middle manifest entries and ledger identity drift;
- direct-SQL timestamp variants and fractional ordering;
- REPLACE/UPSERT with recursive triggers disabled;
- business orphan attempts with foreign keys disabled;
- incomplete lineage finalization;
- foreign-key violation and full transaction rollback.

Failures use fixed messages without database path, payload, credential, URL,
or underlying SQLite exception text. No drop/rebuild recovery masks an
incompatible database.

For a future authorized production rollout, the operational rollback remains
release rollback plus restoration of the pre-migration SQLite backup. v1 does
not contain a down migration that deletes historical state.

The temporary-copy trial is not a generic `migrate(path)` interface. Its
operational proof requires a single prepared workflow:

1. fingerprint a regular, non-symlink source and its WAL/SHM without opening
   the source writable; a non-empty WAL fails closed without checkpointing;
2. create a new current-user mode `0700` run directory below the resolved
   system temporary root;
3. create destination and recovery files with exclusive/no-follow semantics,
   require current ownership, mode `0600`, one link, distinct source inode,
   equal SHA-256, and successful read-only integrity checks;
4. after those checks, scan the private workspace for every directory entry
   whose name starts with the destination or recovery database basename.
   The only allowed companion names are the exact `-wal`, `-shm`, and
   `-journal` names; any other prefixed entry is an unbound companion and
   fails closed without reading, following, deleting, or truncating it.
   Every allowed present sidecar must be a current-user mode `0600`,
   single-link regular file, WAL must be zero bytes, and rollback journal
   must be absent;
5. bind the source, workspace, destination/recovery main files, both complete
   basename companion-name sets, all allowed sidecar fingerprints, process,
   and UID in a `PreparedTrialCopy`;
6. immediately before migration, reject source mutation, workspace/file inode
   replacement, hardlinking, mode/owner drift, SHA drift, any sidecar set or
   content drift, a non-quiescent WAL/journal, or a foreign/raw handle;
7. after the first schema application closes its SQLite connections, rescan
   and bind only the exact expected SQLite companion names before the internal
   no-op application; after that connection closes, reject every unknown
   companion, non-empty destination WAL, or rollback journal, bind the final
   pathset, and prove the recovery main file and companion pathset stayed
   unchanged before returning.

A zero-byte WAL and private SHM created by SQLite read-only access are valid
only when already captured in the prepared sidecar set. SQLite may update SHM
mtime while taking read locks; the post-integrity comparison therefore allows
only that mtime change while continuing to bind the SHM path/device/inode,
owner, mode, link count, size, and SHA, followed by an exact final comparison.
Late appearance, replacement, symlink/hardlink aliasing, logical content in a
WAL, or any basename-prefixed unknown file/directory/FIFO fails closed. The
scanner uses directory-entry names and does not follow or read unknown
entries. The destination (`trial.db`) and recovery (`recovery.db`) use
independent basenames and independently bound pathsets.

The earlier temporary-copy proof bound only the destination/recovery main
files. A later adversarial WAL mutation left the main-file fingerprint
unchanged while changing logical SQLite reads, so the previous broad
“exact-copy fully validated” claim was superseded by this sidecar closure.
That historical P1/P2 finding remains in the audit record.

A still later review found that enumerating only the three known suffixes was
not itself a complete path boundary: names such as `trial.db-wal2`,
`trial.db-mj ABCDEF12`, `trial.db-journal.extra`, or an arbitrary prefixed
symlink were ignored. That P1 is closed by the complete basename scan above;
the history remains recorded in the trial audit.

Observed `/tmp` migration lifecycle evidence is intentionally not an inode
stability promise. One reviewed WAL-mode copy had WAL size 0 and SHM size
32768 before migration, a migration-time WAL peak of approximately 181312
bytes, and a zero-byte WAL with no journal after all connections closed.
SQLite may recreate WAL/SHM inodes. The stable contract is tool ownership of
that lifecycle, committed schema/ledger content in the main file, no non-empty
WAL or journal at return, and a newly bound final companion pathset.

The recovery-image fingerprint and sidecar/pathset safety, an historical
manual `/tmp` file-recovery rehearsal, and clean-image remigration have been
checked. There is no public restore API. Consequently
`PUBLIC_RESTORE_FAILURE_ATOMICITY` remains **UNVERIFIED**: this trial does not
claim that a public restore failure cannot partially replace a target, and it
does not prove a real production restore runbook.

Existing targets and arbitrary caller-supplied destination paths are never
migration inputs. This prepared-copy evidence does not itself authorize a
real production backup or migration procedure.

## Retention and archive

v1 is append-only and defines no routine pruning. Identity, snapshots,
observation events/associations, lineage sets/inputs, and finalized features
remain online. Any future archive policy must first copy a closed range with
hashes and relationship lineage, verify the archive, record an audit event,
and introduce a separately reviewed migration. This design does not authorize
DELETE-based retention.

## Compatibility exclusions

The following remain unchanged and are not production-integrated:

- legacy `dim_match` writers/readers and its mutable `INSERT OR REPLACE`
  behavior;
- `odds.db.dim_match_xref`, `dim_team_xref`, and `poll_state`;
- existing `int_match_features` and `gold_wdl_predictions`;
- FotMob/NowGoal adapters and endpoints;
- Worker registry, systemd units, API routes, and frontend;
- real database data and the sealed CWC runner.
