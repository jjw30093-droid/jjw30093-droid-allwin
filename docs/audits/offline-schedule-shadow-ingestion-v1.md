# Offline schedule shadow ingestion v1 — trust and recovery closure

> Status: **SHADOW_ARTIFACT_TRUST_BOUNDARY_VALIDATED /
> DURABLE_CROSS_PROCESS_SHADOW_RECOVERY_VALIDATED /
> READY_FOR_FINAL_SHADOW_RE_REVIEW**
>
> Scope: immutable saved artifact plus migrated `/tmp` copy only
>
> Production network ingestion, Worker, systemd, API, frontend, real migration:
> **NOT STARTED**

## Historical finding and closure scope

The first `OFFLINE_SCHEDULE_SHADOW_INGESTION_V1_VALIDATED` claim is retained as
historical context, not accepted as current evidence. Independent re-review
found **P0=0 / P1=5 / P2=2 / `FIX_REQUIRED`**:

1. duplicate JSON keys were accepted with default last-write-wins parsing;
2. pagination/completeness was caller self-attestation rather than a raw-payload
   fact;
3. status flags were not governed by an exact supported matrix;
4. `COMPLETED` returned cached manifest data without reconciling database truth;
5. session recovery was bound to creator PID and in-process `_SESSIONS`;
6. artifact type and fixed-size boundaries were incomplete;
7. same-database concurrent processes were neither supported nor rejected.

Permanent RED tests were added first. Against the old implementation, the new
closure file produced **43 collected / 43 selected / 17 passed / 26 failed**,
with 0 skipped and 0 xfailed. The failures directly reproduced duplicate-key
acceptance, missing size/type gates, raw completeness/status gaps, forged
completion, illegal transition gaps, lack of durable descriptor, and lack of
cross-process recovery/locking.

This closure changes no production network or deployment surface. All mutable
databases, descriptors, manifests, and probes remain under `/tmp`.

## Immutable artifact and strict JSON

`load_artifact_envelope()` opens a path once with `O_NOFOLLOW` (or the platform
equivalent) and requires:

- current-UID regular file;
- `st_nlink == 1`;
- no group/world write bit;
- stable device/inode/owner/mode/link-count/size/mtime across the read;
- non-empty size no greater than the fixed 16 MiB limit.

SHA-256 and JSON parsing consume the exact same bytes from that descriptor. The
path is not reopened between hash and parse. SHA mismatch is checked before the
JSON parser is invoked.

The strict parser rejects duplicate members at every object depth even when
the values are equal, NaN, positive/negative Infinity, UTF-8 BOM, invalid
UTF-8, trailing data, top-level non-objects, over-depth structures, and an
explicit total-node budget breach. Errors map to the fixed
`ShadowArtifactError("artifact validation failed")` with no payload, path, URL,
marker, system exception, cause, or context.

Permanent tests cover exact-limit acceptance, limit+1 rejection, symlink and
hardlink rejection, and observation that the parser bytes hash to the accepted
artifact digest.

## Raw artifact provenance and completeness

The formal shadow input is now the sanitized raw-provider projection:

- path:
  `tests/fixtures/fotmob/cwc_2025_competition_schedule_raw.json`;
- projection SHA-256:
  `b2852c04cdcfd812a92164e482309cdca634c3a38dca4973adcf94a3ebbc67fc`;
- recorded saved-source SHA-256:
  `6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d`;
- 66 raw matches under `fixtures.allMatches`;
- no URL, Authorization, credential, proxy, or API-key surface.

The saved source artifact was independently found at the audit-recorded `/tmp`
location and its SHA matched before the projection was created. The projection
preserves raw provider identity, season, match/team IDs, status reason/flags,
kickoff, round, and the complete direct `fixtures` pagination namespace. The
canonical fixture remains byte-for-byte unchanged and is no longer used to
attest raw pagination.

`backend/schedules/pagination.py` is a pure, transport-free single source used
by both the competition pilot and shadow normalization. It retains all prior
alias-aware pair selection, case-fold collision, orphan-marker,
detected+unresolved, malformed value, current>total, multiple-dialect conflict,
hasMore/next/cursor, and fixture-path scoping semantics.

The envelope's completeness evidence is only a declaration. Validation
recomputes `inspect_known_pagination(raw_payload)` and requires the raw result
and every evidence list to agree exactly with `NOT_DETECTED`. A raw DETECTED or
UNRESOLVED result, unknown/invalid marker, collision, orphan, or dialect
conflict fails closed even when the envelope claims `NOT_DETECTED`.

Competition ID/name, returned season, non-empty matches, exact fixture count,
schema version, unique Match IDs, and team IDs are independently cross-checked
against the raw payload. Same-content and conflicting duplicate Match IDs are
both deterministically rejected; input order cannot select a winner.

## Supported FotMob status matrix

All flag values must be actual JSON booleans. The exact supported matrix is:

| status | started | finished | cancelled |
|---|---:|---:|---:|
| `NS` | false | false | false |
| `FT` | true | true | false |
| `AET` | true | true | false |
| `Pen` | true | true | false |
| `Can` | false | false | true |

The CWC raw artifact supplies `FT`, `AET`, and `Can`; the existing competition
fixture supplies the reviewed `Pen` shape. Unknown statuses, unsupported live
states, integer substitutes for booleans, and any contradiction reject the
entire artifact. No status is inferred from the other flags.

## Atomic schedule state and observation semantics

The already-reviewed `record_match_states_batch()` transaction remains the
only state apply path. It atomically creates or exactly reuses stable
identities and immutable snapshots, then records one observation event and all
match associations. Duplicate IDs, same-time business conflict, injected
mid-batch error, or real SQLite failure leaves no partial business rows.

Exact replay creates no duplicate state. A later same-content observation adds
only one event and 66 associations. Out-of-order event-time evidence appends
history without regressing current. A changed match creates one new snapshot
and leaves the prior state. A response missing one previously seen match
creates only the 65 present associations and never infers cancellation,
deletion, or retraction.

## Database truth reconciliation

The manifest is a signed recovery hint, not a database authority.
`derive_shadow_db_state()` performs only queries and classifies:

- `NO_STATE`;
- `STATE_COMPLETE`;
- `FEATURES_COMPLETE`;
- `PARTIAL_OR_CONFLICTING`.

State verification checks the exact run-specific event, every expected
provider identity, canonical identity binding, immutable state-content hash and
business fields, snapshot provenance, observation timestamp, and exact
event/snapshot association. It does not use row counts as a substitute for
content.

Feature verification rebuilds the point-in-time eligible team timelines and
checks each target/team/version/input-set hash, every ordered
identity/snapshot input, input count, feature JSON, feature payload hash,
computation status, and provenance. A fully absent feature set is buildable;
partially present or conflicting lineage fails closed.

Manifest-behind state/feature commits reconcile forward. Manifest-ahead,
partial, and conflicting DB states fail closed. This applies to every
invocation, including `COMPLETED`. A forged `COMPLETED` manifest over an empty
DB and a signed tampered completed result over a real DB are both rejected.

The returned completed result is rebuilt from DB-verified identity, snapshot,
event, association, feature, and lineage-input counts. Manifest insert/skip
deltas remain separate `state_apply_summary` and `feature_apply_summary` audit
fields; they are never returned as DB truth.

## Phase state machine and durable manifest

The only forward path is:

```text
NEW
→ ARTIFACT_VALIDATED
→ STATE_APPLIED
→ FEATURES_APPLIED
→ COMPLETED
```

`FAILED` is legal only from a non-completed phase. Retry restores the recorded
last successful phase and increments the resume count. Every other ordered
phase pair is permanently tested as illegal; no jump, regression, self-write,
or completed overwrite is accepted. Reusing a run ID with different immutable
artifact/envelope identity is a fixed conflict.

Each manifest lives in a `0700` runs directory and is a signed mode-`0600`
single-link regular file. Writes use an exclusive mode-`0600` temp file, file
fsync, atomic replace, and parent-directory fsync. Strict JSON and signature
validation run before an existing manifest can be replaced. Duplicate-key,
corrupt, symlink, hardlink, unknown, and unsigned entries fail closed.
Permanent tests observe both file and parent-directory fsync calls. Manifests
contain no raw fixture, URL, credential, or absolute database path.

## Durable session and cross-process recovery

`open_shadow_session()` consumes the process-bound `PreparedTrialCopy` exactly
once through the reviewed migration API, then creates:

- a private `0700` shadow workspace;
- a signed mode-`0600` session descriptor;
- a mode-`0600` lock file;
- a private runs directory;
- a random 256-bit capability returned separately to the caller.

The descriptor binds the run/workspace/session paths, owner and mode, source,
destination, and recovery main-file fingerprints, complete basename companion
pathsets, schema/ledger, signed manifest pathset, and revision. Creator PID is
signed provenance only and is never a recovery gate. A signed attempt to point
the descriptor at a different DB is rejected by fixed structural path binding.
Wrong capability, descriptor corruption, unknown workspace entry, unknown DB
companion, committed non-empty WAL, journal, schema/ledger drift, source or
recovery drift, and unsafe file types fail closed without modifying the
offending path.

Zero-byte WAL and private SHM are SQLite connection-lifecycle artifacts. A
clean process exit may remove them and a later open may recreate them; this is
accepted only while the main DB is exact, WAL is absent or zero bytes, SHM is
the private expected size, no journal exists, and the complete basename scan
contains no unknown entry. A committed WAL is never rebound.

`reopen_shadow_session()` reconstructs trust from the signed descriptor and
filesystem/SQLite facts. `_SESSIONS` is only a local cache. Real subprocess
tests terminate the creator after:

1. state commit but before manifest advancement; and
2. feature commit but before manifest advancement.

A new Python process reopens and reaches `COMPLETED` without duplicate
snapshots, lineage inputs, or features.

`flock(LOCK_EX | LOCK_NB)` is held across validation, reconciliation, state
apply, feature apply, and manifest completion. A second process—regardless of
run ID—gets the fixed `shadow session is already active` error, performs zero
business writes, and can proceed only after lock release.

`CONCURRENT_SHADOW_RUNS: UNSUPPORTED_BY_DESIGN`.

## CWC and lineage regression

The raw-provider projection preserves the previously validated business output:

- Run 1: 66 identities, 66 snapshots, 1 event, 66 associations;
- 63 non-cancelled and 3 explicit cancelled matches;
- 126 finalized rest features and 334 ordered lineage inputs;
- Manchester City `NULL / 105 / 90 / 102`;
- AET retained;
- cancelled matches excluded from feature targets and inputs.

Run 2 at the later synthetic observation adds one event and 66 associations,
with no new identity/snapshot or feature version. A test-only derived artifact
changes match 4685746's kickoff and adds exactly one snapshot; only the related
teams and downstream target hashes version. The raw and canonical repository
fixtures are never modified.

Six fault boundaries cover post-artifact, mid-state transaction, post-state
commit/pre-manifest, post-state phase, mid-feature transaction, and
post-feature commit/pre-manifest. Feature failure preserves committed
source-truth state but leaves no partial lineage set/input/feature.

## Acceptance evidence

Every Python command used `PYTHONDONTWRITEBYTECODE=1` and the same isolated
`PYTHONPYCACHEPREFIX`; every pytest command used `-p no:cacheprovider` and
`-W default`.

| execution | collected | selected | passed | failed | skipped | xfailed | warnings |
|---|---:|---:|---:|---:|---:|---:|---:|
| strict JSON / immutable artifact | 93 | 16 | 16 | 0 | 0 | 0 | 0 |
| raw pagination / completeness | 93 | 11 | 11 | 0 | 0 | 0 | 0 |
| status matrix | 93 | 7 | 7 | 0 | 0 | 0 | 0 |
| manifest / DB reconciliation | 93 | 3 | 3 | 0 | 0 | 0 | 0 |
| legal phase state machine | 93 | 44 | 44 | 0 | 0 | 0 | 0 |
| cross-process recovery / descriptor | 93 | 7 | 7 | 0 | 0 | 0 | 0 |
| cross-process lock | 93 | 1 | 1 | 0 | 0 | 0 | 0 |
| manifest durability | 93 | 4 | 4 | 0 | 0 | 0 | 0 |
| both shadow test files | 132 | 132 | 132 | 0 | 0 | 0 | 0 |
| competition pagination full file | 193 | 193 | 193 | 0 | 0 | 0 | 0 |
| schedule-state schema | 123 | 123 | 123 | 0 | 0 | 0 | 1 |
| migrations + contract | 50 | 50 | 50 | 0 | 0 | 0 | 1 |
| CWC prototype | 76 | 76 | 76 | 0 | 0 | 0 | 0 |
| network hard block | 39 | 1 | 1 | 0 | 0 | 0 | 0 |

The two warnings are the same pre-existing collection-time
`StarletteDeprecationWarning` about the installed httpx/TestClient integration.
No test warning was filtered. Static review found no skip, xfail, warning-filter,
or module-level pytest marker in either shadow permanent test file.

The isolated `/tmp` compileall returned 0. `git diff --check` and the separate
whitespace check for every new/changed untracked closure file returned clean.
The historical RED result remains **43 collected / 43 selected / 17 passed /
26 failed**, not replaced by the green acceptance.

## Integrity and retained limits

Closing branch `main`, HEAD
`cfe027283ab6318a1298d89d544eb9fa351fa713`, no exact tag, and empty stash
match the task baseline. Git's existing dirty status path surface is unchanged;
new paths are confined to the authorized closure module, shared backend
helpers, raw projection, permanent tests, and documentation.

All four real database SHA/size/mtime/inode fingerprints and every existing or
absent WAL/SHM state match baseline. The canonical fixture remains SHA-256
`020b6eabecd9ca004611863d3b3f5820ee12ea2f1ff8f203f51e73a1bbf276b1`;
the six saved artifact fingerprints are unchanged.

Repository cache count remains 109 `__pycache__` directories and 812 `.pyc`
files. Cache path and content digests, `.pytest_cache`'s 9-entry path/content
and metadata digests, and the ignored worktree pathset are unchanged. No cache
was deleted or restored. `CURRENT_SHADOW_INGESTION_INTEGRITY: PASS`.

The closure has **P0=0 / P1=0**:

- `SHADOW_ARTIFACT_TRUST_BOUNDARY_VALIDATED`;
- `DURABLE_CROSS_PROCESS_SHADOW_RECOVERY_VALIDATED`;
- `READY_FOR_FINAL_SHADOW_RE_REVIEW`.

The following limits remain unchanged:

- `HISTORICAL_PYCACHE_INTEGRITY_EVENTS: FAIL (2)`;
- `PUBLIC_RESTORE_FAILURE_ATOMICITY: UNVERIFIED`;
- Historical state backfill: `BLOCKED`;
- current live provider behavior: `UNVERIFIED`;
- Production network ingestion, persistent polling, Worker, systemd, API,
  frontend, and real migration: **NOT STARTED**.

This module does not authorize a live shadow request.
