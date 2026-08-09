# Production schedule-state schema v1 offline audit

> Date: 2026-07-26
> Scope: formal schema, migration, state transition, projection, and lineage
> proof in newly created temporary SQLite only
> Production ingestion / Worker / API / frontend: **NOT STARTED**

## Verdict boundary

The prior prototype is closed and remains historical evidence. Its earlier
`FIX_REQUIRED` findings for deterministic helper input and timestamp exception
chaining remain recorded in `cwc-production-integration-design.md`; both were
closed before this module began.

This module does not authorize a production source, real-data migration, live
request, Worker, systemd, API, or frontend integration. The CWC runner remains
permanently sealed.

## Independent repository audit

`rg` and source inspection established:

- canonical FotMob `Match_ID`, legacy `dim_match`, `int_match_features`, and
  existing model inputs are in core / `allwin.db`;
- cross-provider `dim_match_xref` and `dim_team_xref`, plus collection
  throttle `poll_state`, are in `odds.db`;
- legacy `dim_match` mixes stable ID, mutable kickoff/status/team fields, and
  display names, and its active writers use `INSERT OR REPLACE`;
- existing readers assume legacy `dim_match` current-state semantics;
- SQLite cannot enforce cross-file foreign keys.

Therefore v1 is a new core migration that coexists with `dim_match`. It does
not alter or backfill that table and does not move xref or poll state.

An immutable read of the four real database schemas also corrected stale
historical documentation: `data/allwin.db` currently records only core
migration `0001`, and its `dim_match` has `kickoff_at_utc` but not
`kickoff_precision` / `kickoff_source`. No real database was upgraded. A
permanent test instead reconstructs that actual version-1 shape in a new
temporary database and proves the consecutive `0002` + `0003` upgrade. None
of the four real databases contains a `schedule_*` object.

## Delivered schema

`backend/migrations/core/0003_schedule_state_v1.sql` adds:

- `schedule_match_identity`;
- `schedule_match_state_snapshot`;
- `schedule_observation_event`;
- `schedule_match_observation`;
- `current_schedule_match_state`;
- `schedule_rest_lineage_set`;
- `schedule_rest_lineage_input`;
- `schedule_rest_feature`;
- read-only finalized-lineage view `schedule_rest_feature_input`;
- required indexes and direct-SQL append-only/reference/finalization triggers.

`backend/schedules/state.py` supplies:

- migration application plus exact `sqlite_master` validation;
- atomic identity/state/observation recording;
- deterministic current and as-of queries;
- fixed-width UTC canonicalization;
- deterministic rest input hashing and build→inputs→finalize persistence;
- point-in-time observed rest-feature batch construction.

No module imports `analysis.*`. No production adapter imports this module.

## Migration proof

Permanent tests execute all migration work on new pytest temporary databases:

- fresh database and exact DDL validation;
- legacy core → v1 with `dim_match` columns/row unchanged;
- second-run idempotency;
- partial schema;
- wrong same-name view;
- post-application extra-object drift;
- weakened constraint catalog;
- mid-migration failure;
- foreign-key mismatch;
- fixed error boundary with no path/payload marker or lower exception chain.

Incompatible states fail closed. The failed migration transaction leaves no v1
object or version row. No test drops/rebuilds an incompatible database to make
it pass.

## State and projection proof

Synthetic transition coverage includes:

- initial NS and NS→FT;
- kickoff moved later and earlier;
- postponed;
- cancelled;
- cancelled→scheduled recovery;
- TBD→concrete team;
- round and stage correction;
- same state at later and earlier observation times;
- different earlier state ingested late without current regression;
- same event-time business conflict;
- exact repeated poll;
- forced transaction failure.

New business content appends a snapshot. Repeated business content reuses its
snapshot and appends only observation evidence. Returning to an earlier state
reuses that historical snapshot. Current and as-of selection use observation
event time, with observation ID only as a stable final tie-breaker.

Database triggers reject UPDATE/DELETE of identity, state, observation,
feature, and feature-input history.

## Independent adversarial reversal and direct-SQL closure

The first `READY_FOR_INDEPENDENT_SCHEMA_REVIEW` conclusion was not accepted as
self-proof. Independent source/runtime probes overturned it with
**P0=0 / P1=5 / P2=1**:

1. `[0001,0003]` was treated as a valid migration manifest and version 3 was
   recorded without version 2.
2. Python emitted variable-width UTC text. A zero-microsecond `...00Z` sorted
   after the semantically later `...00.500000Z`, so the TEXT current
   projection selected the older snapshot; raw SQL also accepted offsets,
   naive values, trailing spaces, and invalid calendar dates.
3. With SQLite's default `recursive_triggers=OFF`, `INSERT OR REPLACE` could
   delete/reinsert immutable identity, snapshot, observation, and feature
   rows without firing DELETE guards.
4. With `foreign_keys=OFF`, scalar-subquery comparisons could evaluate to
   SQL `NULL`, allowing orphan observation/feature relationships.
5. A `schedule_rest_feature` header declaring two inputs could commit with
   zero actual inputs and appear consumable.
6. A ledger filename could differ from the current manifest filename while
   version and checksum matched.

Permanent tests were written before implementation. The old implementation
produced:

- manifest/identity RED: **19 collected / 5 selected / 0 passed / 5 failed /
  14 deselected**;
- direct-SQL RED: **56 collected / 17 selected / 0 passed / 17 failed /
  39 deselected**.

Each process emitted one existing SeleniumBase legacy-hook
`PytestDeprecationWarning`; no warning filter was used.

The closure establishes:

- a non-empty migration manifest continuous from version 1, validated before
  opening a writable DB;
- exact migration identity `(version, filename, checksum)`, including applied
  ledger versions that must still exist in the manifest;
- canonical `YYYY-MM-DDTHH:MM:SS.ffffffZ` for every ordering/evidence
  timestamp, independently enforced by SQLite CHECK constraints;
- BEFORE INSERT primary/natural-key conflict guards on every append-only
  layer, so REPLACE is rejected even after a raw caller sets
  `recursive_triggers=OFF`;
- `NOT EXISTS` relationship guards that reject business orphans even after a
  raw caller sets `foreign_keys=OFF`;
- separate immutable observation events and per-match associations;
- separate immutable lineage set and ordered inputs, followed by a finalized
  feature that cannot be inserted until count/order/identity/snapshot/target
  checks all pass.

The normal service enables and verifies both `foreign_keys` and
`recursive_triggers`, performs exact pre-insert idempotency lookups, and writes
lineage set → inputs → finalized feature in one transaction. Forced
second-input failure leaves zero rows in all three layers and exposes only a
fixed project exception with no lower `cause` or `context`.

## CWC and lineage proof

The permanent canonical CWC fixture is used only as offline semantic input:

- 66 identities / current states;
- 66 initial snapshots and observations;
- 63 non-cancelled, 3 cancelled;
- 126 observed rest features;
- Manchester City `NULL / 105 / 90 / 102` hours;
- final `AET` retained;
- cancelled snapshots absent from feature inputs.

Later identical observation creates 66 ledger rows, zero snapshots, and zero
new feature versions. Changing the final Manchester City kickoff adds lineage
only for the final target; changing the second kickoff adds versions for the
second and downstream targets, not the first. A future match in an early
feature prefix is rejected. Same ordered input remains idempotent across
different computation/as-of wall clocks.

## Compatibility and retention

The migration text contains no `ALTER/UPDATE dim_match`, `dim_match_xref`, or
`poll_state`. Static scans confirm no Worker, API, systemd, or frontend
reference to the new current/snapshot objects.

v1 defines no pruning. Archive/delete policy requires a future reviewed
migration with copied hashes, preserved foreign-key lineage, verification, and
audit evidence.

Detailed roles, keys, projection ordering, conflict semantics, and rollback
policy are in
`docs/architecture/production-schedule-state-schema.md`.

## Test evidence

The first implementation target exposed two real defects:

- migration errors retained the lower SQLite `__context__`;
- the as-of query did not expose an explicit `snapshot_id` alias.

After fixing both and adding direct database-trigger plus real-v1-shape
coverage:

| Scope | Collected | Selected | Passed | Failed | Skipped | Xfailed | Warnings |
|---|---:|---:|---:|---:|---:|---:|---:|
| manifest/gap target | 20 | 8 | 8 | 0 | 0 | 0 | 1 |
| timestamp direct-SQL target | 75 | 17 | 17 | 0 | 0 | 0 | 1 |
| REPLACE/UPSERT target | 75 | 8 | 8 | 0 | 0 | 0 | 1 |
| FK-off target | 75 | 5 | 5 | 0 | 0 | 0 | 1 |
| feature finalization target | 75 | 5 | 5 | 0 | 0 | 0 | 1 |
| current/as-of target | 75 | 8 | 8 | 0 | 0 | 0 | 1 |
| schedule-state full | 75 | 75 | 75 | 0 | 0 | 0 | 1 |
| CWC prototype | 76 | 76 | 76 | 0 | 0 | 0 | 1 |
| sealed CWC pilot | 41 | 41 | 41 | 0 | 0 | 0 | 1 |
| team + competition pilots | 286 | 286 | 286 | 0 | 0 | 0 | 1 |
| migrations + contract | 50 | 50 | 50 | 0 | 0 | 0 | 1 |

Deselected items in focused runs are not skips or xfails. The warning is the
same existing SeleniumBase legacy-hook `PytestDeprecationWarning` emitted once
per pytest process; it is not multiple product defects. No warning filter was
used. `/tmp`-isolated compileall and
`git diff --check` both exit 0.

## Integrity evidence

The end comparison retained branch `main`, HEAD
`cfe027283ab6318a1298d89d544eb9fa351fa713`, no exact tag, and an empty
stash. All four real database SHA-256/size/mtime tuples, the existing
allwin/odds WAL/SHM files, canonical fixture, and all six mode-0600 historical
artifacts match the opening baseline. Repository cache remains at 107
`__pycache__` directories, 808 `.pyc` files, and eight `.pytest_cache`
descendants (nine paths including the cache root); content and metadata
digests are unchanged.

The direct-SQL closure opened after the schema module's authorized paths
already existed. Excluding `.git`, its round-local worktree pathset therefore
remained exactly 40,516 paths with the same digest; the untracked set remained
47 paths with the same digest. Existing dirty/untracked assets were preserved.
The Git status digest changed only because the authorized tracked migration
runner and permanent migration tests entered modified state; it is not
misreported as an unchanged status.
No network, credential, real-database write, commit, push, tag, deploy, stash,
or cleanup occurred.

## Current status

- Stable identity schema: **VALIDATED**
- Append-only state snapshots: **VALIDATED**
- Observation ledger: **VALIDATED**
- Fixed-width timestamp DB enforcement: **VALIDATED**
- REPLACE resistance: **VALIDATED**
- FK-off business integrity: **VALIDATED**
- Deterministic current projection: **VALIDATED**
- As-of query: **VALIDATED**
- Build/finalize rest-feature lineage: **VALIDATED**
- Migration manifest/identity: **VALIDATED**
- Migration/rollback: **VALIDATED**
- Legacy compatibility: **VALIDATED**
- Integrity: **PASS**
- Production ingestion: **NOT STARTED**
- Worker/systemd: **NOT STARTED**
- API/frontend: **NOT STARTED**
- Real database migration: **NOT STARTED**

Final module state:
`PRODUCTION_SCHEDULE_STATE_SCHEMA_V1_DIRECT_SQL_VALIDATED` /
`READY_FOR_FINAL_INDEPENDENT_SCHEMA_RE_REVIEW`.
