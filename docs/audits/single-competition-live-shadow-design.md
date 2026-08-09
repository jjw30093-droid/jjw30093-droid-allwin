# Single-competition live-shadow acquisition design

> Status: **SINGLE_COMPETITION_LIVE_SHADOW_DESIGN_VALIDATED_OFFLINE /
> DURABLE_NETWORK_REQUEST_BUDGET_DESIGN_VALIDATED /
> READY_FOR_EXPLICIT_SINGLE_COMPETITION_NETWORK_AUTHORIZATION**
>
> Validation date: 2026-07-28
>
> Network requests made by this module and review: **0**
>
> Current module integrity: **PASS**

## Scope and non-authority

This module is an offline design proof around the independently validated
schedule-shadow ingestion boundary. It supplies no live transport. Permanent
tests inject `FakeTransport` while hard-blocking AF_INET, AF_INET6, DNS,
`urllib`, `requests`, and `curl_cffi`; local AF_UNIX behavior remains
available for the test runtime.

The proof does not authorize a provider request, competition, season, request
budget, proxy, or credential. A future user decision must explicitly provide
all of those authorities. The former sealed CWC 3/3 request budget is not
reused or reinterpreted. Design validation is not live-ingestion validation.

Production network ingestion, Worker, systemd, API, frontend, and real
migration remain **NOT STARTED**. Historical state backfill remains
**BLOCKED**. `PUBLIC_RESTORE_FAILURE_ATOMICITY` remains **UNVERIFIED**.
`CONCURRENT_SHADOW_RUNS` remains **UNSUPPORTED_BY_DESIGN**.

## Phase 0: Allsvenskan read-only status

The host review distinguished code, configured scheduling, actual scheduling,
historical execution, and recent data:

- The repository contains worker job registrations and
  `deploy/systemd/allwin-poll.timer`/service files. This proves code and
  deployable configuration exist.
- This macOS host had no relevant running Python process, launchd job, cron
  entry, Docker container, or systemd runtime. `systemctl` and Docker were not
  available; launchd contained no allwin/FotMob/NowGoal/Allsvenskan job.
- Immutable reads found no Allsvenskan rows in real core data and zero rows in
  the real odds `bronze_ng_odds_snap`, `silver_odds_moves`,
  `dim_match_xref`, `poll_state`, and `source_health` tables.
- The real platform job ledger contained one successful
  `nowgoal_snapshot` at 2026-07-19T14:37:06Z and one failed
  `fotmob_snapshot` at 2026-07-19T14:37:07Z. These isolated attempts do not
  prove continuous operation.
- Surviving `/tmp/allwin-allsvenskan-*` directories had empty data
  directories. The remaining backend/frontend logs ended on 2026-07-21 and
  described a local isolated experiment, not a persistent collector.
- No evidence proves a real odds snapshot on the immediately preceding
  weekend, 2026-07-25 through 2026-07-26.

Therefore the Allsvenskan collector was **not proven to be continuously
running**, and last-weekend real odds snapshots are **not present in the
reviewed real databases**. Repository text refers to competition/provider ID
67, but the saved experimental database/raw evidence needed to independently
re-prove that identity is absent and network access was prohibited. The ID is
therefore **UNVERIFIED IN THIS REVIEW**, not guessed.

## Explicit acquisition policy

`AcquisitionConfig` fixes one provider, competition ID/name, requested season,
allowed operation tuple, budget maximum, expected fixture count, competition
class, and artifact schema version. Any operation outside the tuple is
rejected before transport. There is no daily discovery, check-IP, proxy probe,
health call, or implicit provider client construction.

The only transport interface used by the proof has one operation:

```text
request(operation, competition_id, requested_season) -> raw bytes
```

`FakeTransport.calls` proves each handoff. The implementation source contains
no socket, HTTP client, URL, proxy, or credential access and is not registered
in any Worker, systemd, migration, API, or frontend surface.

## Durable request budget and outcome semantics

A private SQLite control database, stored only in the prepared `/tmp`
workspace, durably records:

- `run_id` and unique `request_id`;
- competition identity, requested season, and operation;
- attempt ordinal and budget maximum;
- canonical intent-recorded timestamp;
- dispatch and response-receipt states;
- response SHA/size and bound artifact SHA/size;
- fixed terminal outcome.

The intent row commits before dispatch. Transition to `DISPATCH_STARTED` also
commits before control reaches the injected transport, so every possible
low-level attempt consumes budget. A known no-response `FakeTransportFailure`
becomes `FAILED_SAFE`; a retry creates the next ordinal and consumes another
unit. Exhaustion is checked before transport and is durable across process
restart.

If execution stops after possible dispatch but before durable response receipt,
the attempt is conservatively reconciled to `OUTCOME_UNKNOWN`. The run then
rejects every automatic retry before transport. An unexpected transport
exception is treated the same way. Human intervention is required outside
this design.

The control database, signed session descriptor, artifact ledger, and fixed
exceptions store no raw response, URL, Authorization value, proxy string,
credential, or unsafe transport exception. Public errors discard cause and
context.

## Private immutable response artifact

Accepted raw bytes are bounded by the existing 16 MiB shadow-artifact limit.
They are written to an exclusive mode-`0600` staging file, fsynced, followed by
parent-directory fsync, then atomically renamed and followed by another
directory fsync. The bound final path must be a current-UID regular,
single-link mode-`0600` file. Its SHA-256 and size must equal both the durable
attempt and artifact ledgers.

A crash after receipt resumes from the durably staged bytes without transport.
A crash after rename but before ledger binding accepts the final file only if
its fingerprint exactly matches the staged ledger. Tampering rejects before
business apply. Completed replay revalidates the artifact and asks the
existing shadow state machine to rebuild database truth rather than returning
only a cached control result.

Hashing and strict parsing are not duplicated. The final artifact is handed to
the existing same-file-descriptor `load_artifact_envelope()`, shared raw
pagination inspector, exact status matrix, and normalization path. DETECTED or
UNRESOLVED pagination, duplicate JSON members, status contradiction, fixture
count/identity/season/schema mismatch, and artifact drift remain fail-closed.

## Prepared copy, state apply, and lineage

`prepare_acquisition_session()` accepts only a real
`PreparedTrialCopy`. It first invokes the validated migration/session
boundary; a raw database path is rejected. Business writes are therefore
limited to that guarded temporary copy.

After artifact validation, the existing offline shadow ingestion remains the
only apply engine. It preserves:

- atomic identity/snapshot/observation transactions;
- exact replay and later/out-of-order observation behavior;
- no cancellation/deletion inference from absence;
- immutable changed-state versions;
- feature transaction retry without another transport call;
- versioned point-in-time rest-feature lineage with no future, unfinished, or
  cancelled input.

The offline CWC handoff reproduced 66 identities/snapshots, 126 rest features,
and 334 lineage inputs. Later same-content evidence added observation history
without duplicating snapshots/features. A one-match kickoff change added a new
snapshot and only versioned downstream feature rows while retaining old rows.

## Cross-process recovery and concurrency

The private acquisition descriptor is atomically written and signed with the
same separately returned 256-bit capability used by the shadow session.
Reopen reconstructs trust from signed paths, private file/directory properties,
the exact control schema/config digest, and the underlying durable shadow
descriptor.

Real subprocess tests terminated the creator at seven boundaries:

| Boundary | Recovery contract |
|---|---|
| intent committed, before transport | same intent safely dispatches once |
| possible transport, before receipt | `OUTCOME_UNKNOWN`, zero retry calls |
| receipt staged, before rename | staged bytes resume, zero transport calls |
| rename, before artifact ledger | exact final fingerprint reconciles |
| artifact validated, before apply | bound artifact resumes apply |
| state commit, before shadow manifest | database truth reconciles forward |
| feature commit, before final manifest | feature truth reconciles forward |

The acquisition lock is a non-blocking cross-process exclusive `flock`.
The second process is rejected before any transport call. This is deliberate
single-writer exclusion, not support for concurrent shadow runs.

## Permanent RED and closing verification

The permanent test was created before the implementation existed. Its first
execution stopped during collection with one import error and no tests
executed, proving the module boundary was absent.

Closing commands, all with `PYTHONDONTWRITEBYTECODE=1`, one isolated
`PYTHONPYCACHEPREFIX`, `-p no:cacheprovider`, and `-W default`:

- acquisition design: **26 collected / 26 selected / 26 passed**;
- offline shadow ingestion + trust/recovery: **132 / 132 / 132 passed**;
- temporary-copy migration trial: **55 / 55 / 55 passed**;
- schedule-state + migrations + contract: **173 / 173 / 173 passed**;
- sealed CWC pilot + production-integration prototype: **117 / 117 /
  117 passed**.

All commands had 0 failed, skipped, and xfailed. The schedule-state +
migrations + contract command reported one existing
`StarletteDeprecationWarning` about the Starlette/httpx test-client adapter;
all other commands reported zero warnings. No warning filter, skip, xfail,
live client, production transaction mock, or fixture rewrite was used.

The isolated compileall and whitespace checks are recorded in the closing
task report. Network request count remained exactly zero.

## Integrity and retained blockers

The authorized new paths are this analysis module, its permanent tests, this
audit, and the synchronized plan/current-state entries. Closing comparison
retained branch `main`, HEAD
`cfe027283ab6318a1298d89d544eb9fa351fa713`, no exact tag, and an empty
stash. The pre-existing Git status digest remained identical. After excluding
the four authorized new files, the untracked NUL pathset digest remained
`7e7284abead0656ec74c3ef15ee4bc1541a81a57fcef5786ade76875b36176e3`.
The pre-existing `.git`-excluded worktree pathset remained 40,536 entries with
digest
`dc18501b4d263109b060ded0ec01ae5070fc375ffb3461d2b45db152aaaa1ac5`.

All four real database main files and existing WAL/SHM fingerprints, both CWC
fixtures, and all six historical sealed artifacts matched the opening
SHA/size/mtime/inode/mode evidence. Repository cache stayed at 109
`__pycache__` directories and 812 `.pyc` files with identical path, content,
and metadata digests. `.pytest_cache` remained nine paths with identical path
and content digests; the ignored pathset was also identical. Therefore
`CURRENT_MODULE_INTEGRITY: PASS`.

The two historical repository-cache incidents are permanently retained:

`HISTORICAL_PYCACHE_INTEGRITY_EVENTS: FAIL (2)`.

They are not deleted, touched, restored, or relabelled by this module.
Regardless of the offline design result:

- live transport: **NOT AUTHORIZED**;
- target competition: **NOT AUTHORIZED**;
- future request budget: **REQUIRES EXPLICIT USER AUTHORIZATION**;
- Historical state backfill: **BLOCKED**;
- `PUBLIC_RESTORE_FAILURE_ATOMICITY`: **UNVERIFIED**;
- Production network ingestion / Worker / systemd / API / frontend / real
  migration: **NOT STARTED**.
