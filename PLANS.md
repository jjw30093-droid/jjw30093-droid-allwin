> **本文件是"模块验收契约与独立复核记录"的索引，不是计划文件。**
> 数据层的当前状态与前向计划 → `docs/data-plan.md`(单一真源)。
> 逐轮执行记录 → `docs/current-state.md`。来源能力 → `docs/data-sources.md`。
> 新增条目只写：目标 / 锁定边界 / 验收命令 / 独立复核结论 / Unverified。
> **不要**在这里写排期、优先级或"下一步做什么"——那些只写 `docs/data-plan.md` §5。

---

# Validated Module: Kbisai public live-score provider v1

## Status

KBISAI PUBLIC REST SNAPSHOT: VALIDATED /
KBISAI PUBLIC WEBSOCKET HANDSHAKE + SUBSCRIPTION: VALIDATED /
REAL SNAPSHOT: 344 MATCHES / 14 IN PLAY /
CURRENT MODULE CACHE INTEGRITY: FAIL (6 VENV PYCACHE DIRS / 54 PYC ADDED) /
REAL DATABASE WRITE: NOT RUN /
PRODUCTION WORKER / API / FRONTEND / DEPLOYMENT: NOT RUN

## Contract

The provider uses only the anonymous football surfaces called by the public
`kbisailive.com` Web client: `POST /api/v1/football/realtimeMatch_b` for a
bounded full snapshot and `wss://kbisailive.com/ws/match` for optional type
10 subscription with type 12/13 score/status events. It does not read cookies,
accounts, captchas, paid content, `.env`, or proxy credentials.

The REST and WebSocket Protobuf contracts are decoded locally with explicit
size/field/string budgets. Only competition, team, match, status, score,
kickoff and public clock-reference fields enter the canonical projection;
anchor, room, chat, odds and account fields are skipped. The source's clock
reference has epoch-like values but its per-status minute semantics are
unverified, so it is not labelled as a match minute.

The real 2026-07-31 local run returned 105 competitions, 686 teams and 344
matches, including 14 in play. A real WebSocket connection and football-list
subscription succeeded; no type 12/13 change occurred during the bounded
10-second observation. Raw Protobuf and normalized JSON are mode 0600 under
gitignored `runtime/research/kbisai-live-scores/`. No existing SQLite database
was written.

Strict cache integrity is separately FAIL: the final acceptance process created
six `__pycache__` directories and 54 `.pyc` files under `.venv` for
requests/urllib3/charset_normalizer, changing the prior 109/813 count to
115/867. No source-tree bytecode was added. The files were not deleted,
touched, or falsely restored.

Operations: `docs/operations/kbisai-live-scores.md`.
Evidence: `docs/audits/kbisai-live-score-provider-v1.md`.

---

# Validated Research Module: Multi-league season coverage probe v1

## Status

MULTI_LEAGUE_SEASON_COVERAGE_PROBE_VALIDATED /
CROSS_YEAR_SEASON_RESOLUTION_VALIDATED /
READY_FOR_ISOLATED_HISTORICAL_BACKFILL /
REAL HISTORICAL BACKFILL: NOT RUN /
REAL DATABASE MIGRATION: NOT RUN /
PRODUCTION WORKER / API / FRONTEND / DEPLOYMENT: NOT RUN

## Contract

The gitignored durable run covers MLS, J. League, K League 1, A-League,
Eredivisie, Championship, Liga Portugal, Brazil Serie A, Champions League,
Europa League, Conference League, and three controls. Competition identity,
provider-advertised season, exact returned season, pagination, fixtures,
deterministic samples, field paths, structure, safe-start evidence, retries,
request budget, resume, and zero-network replay are validated before any
historical backfill design.

Calendar seasons are distinct from cross-year and tournament seasons.
Cross-year labels are never inferred from month. J. League's real advertised
`2026/2027` transition is retained as tournament-season evidence beside its
calendar history. Three match samples per completed season can yield only
`SAMPLED_SAFE`; they do not prove full-season completeness.

Evidence:
`docs/audits/multi-league-season-coverage-probe-v1.md`.

---

# Validated Module: Studio Douyin-safe team-style cards

## Status

DOUYIN-SAFE STUDIO PROFILE: IMPLEMENTED /
SIX TEAM-STYLE SCENES: IMPLEMENTED /
REAL ELITESERIEN REPLAY: ZERO NETWORK /
TWELVE FINAL PNG EXPORTS: VISUALLY VALIDATED /
INTERNAL FULL ANALYSIS MODE: PRESERVED /
REAL DATABASE MIGRATION: NOT RUN /
AWS DEPLOYMENT: NOT RUN

## Product contract

Studio defaults to `douyin-safe-v1` when a bound team-style profile is
available. The safe profile is an allowlist-only private view model: it
contains team identity, crests, season context, style metrics, neutral
conclusions and safe social copy, and physically excludes prediction, 1X2,
odds, market-baseline and recommendation fields. The pre-existing full
analysis profile remains available to analysts and its export contract is
unchanged.

The six scenes are cover, possession/organization, box threat,
width/set-pieces, off-ball/defensive pressure, and summary/risk. Each scene
uses at most one comparison treatment and three paired metrics. Set pieces are
labelled `定位球进球`; no set-piece xG claim is made. Missing values reduce
the metric count and are never rendered as zero.

The real Eliteserien replay reads already-saved home/away FotMob team
artifacts, validates team/league/season/sample/source bindings, appends a
versioned runtime-only `team_style_profile`, and writes safe JSON, SRT,
45–60-second voiceover, titles, Xiaohongshu text and WeChat summary with zero
network requests. The platform migration is applied only to runtime/E2E
databases; the real SQLite databases remain untouched.

Full evidence: `docs/audits/douyin-safe-studio-v1.md`.

---

# Validated Module: Unified team crest pipeline

## Status

UNIFIED TEAM CREST PIPELINE: IMPLEMENTED /
REAL ELITESERIEN 2026 SYNC: 16 OF 16 /
SYNC FAILURES: 0 /
SECOND RUN REQUESTS: 0 /
LOCAL SAME-ORIGIN MEDIA API: IMPLEMENTED /
TEAM BADGE UI: IMPLEMENTED /
AWS DEPLOYMENT: NOT RUN

## Product contract

`backend.cli.sync_team_crests` reads deduplicated provider-aware team IDs from
the existing schedule database and is the only remote acquisition entry point.
It validates FotMob PNG bytes, installs them and a SHA-bound manifest under
`ALLWIN_MEDIA_DIR` with fsync plus atomic rename, and preserves valid prior
content on refresh failure. Ordinary API requests never contact FotMob.

Shared `TeamRef.crest_url` values are nullable, same-origin, and content
versioned. The media route serves only a manifest-bound regular single-link
file whose SHA, dimensions and URL version match; the media root,
`team-crests`, and provider directory must also be real non-symlink
directories. Home, matches, detail and standings use the single `TeamBadge`
component and its stable fallback.

The real Eliteserien 2026 run selected 16 distinct teams, downloaded 16 valid
192×192 PNG files in 16 requests, and recorded no unavailable teams. The
immediate idempotent rerun skipped all 16 with zero network requests. Runtime
media is gitignored and no existing SQLite database was modified.

Runbook: `docs/operations/team-crest-sync.md`.

---

# Validated Module: Fresh daily active-league content pipeline

## Status

FRESH_DAILY_CONTENT_PIPELINE_READY /
ACTIVE_LEAGUE_MVP_READY /
DAILY_STUDIO_WORKFLOW_READY /
AWS_DEPLOYMENT_PACKAGE_READY /
CONTENT HORIZON: 7 DAYS /
ODDS HIGH-FREQUENCY WINDOW: T-72H TO KICKOFF /
REAL SAMPLE: ELITESERIEN 2026, FOTMOB 5104968, NOWGOAL 2912857 /
REAL PROVIDER ATTEMPTS: 22 OF 50 /
REAL LEGACY DATABASE MIGRATION: NOT RUN /
AWS / SYSTEMD / CLOUDFLARE INSTALLATION: NOT RUN

## Product contract

`scripts/daily_content.sh` is the single operator entry point. `--fresh`
validates Eliteserien competition 59/name/2026 season, selects a real match
inside the seven-day content horizon, verifies the FotMob/NowGoal direction
and kickoff, saves immutable raw responses, applies the existing isolated
schedule/odds/analysis pipeline, and refreshes Studio sources. `--replay`
verifies and replays the last successful artifact set with zero network.
`--due --dry-run` evaluates the persisted odds schedule with zero network.

Content selection and odds polling are deliberately separate. A real match
outside 72 hours remains eligible for the website and Studio. Odds may take
one initial snapshot above 72 hours, then poll at 15-minute intervals from
T-72h to T-2h and five-minute intervals until kickoff. In-play is excluded;
unchanged payloads do not create a fake second business observation.

The real sample is Vålerenga v Hamarkameratene at
`2026-07-31T17:00:00Z`: FotMob `5104968`, NowGoal/Titan `2912857`, exact
home/away direction, zero-second difference and confidence 1.0. Anonymous API
output physically contains one configured probability; Premium contains all
three probabilities and the real 1X2/AH/OU timeline. The source is explicitly
`MARKET_BASELINE`.

Local durable defaults are the gitignored `runtime/` tree. Production
directories, request ceiling, horizon and API base are configured by
environment. systemd, EnvironmentFile, logrotate, backup/health/retention,
AWS Tokyo permissions and Cloudflare origin guidance are prepared but were not
installed or deployed. Evidence is in
`docs/audits/active-league-content-mvp.md` and
`docs/operations/fresh-daily-content.md`.

---

# Validated Prerequisite: Single-competition live-shadow acquisition design

## Status

SINGLE_COMPETITION_LIVE_SHADOW_DESIGN_VALIDATED_OFFLINE /
DURABLE_NETWORK_REQUEST_BUDGET_DESIGN_VALIDATED /
READY_FOR_EXPLICIT_SINGLE_COMPETITION_NETWORK_AUTHORIZATION /
NETWORK REQUESTS THIS MODULE: 0 /
CURRENT_MODULE_INTEGRITY: PASS /
LIVE TRANSPORT: NOT AUTHORIZED /
TARGET COMPETITION: NOT AUTHORIZED /
FUTURE REQUEST BUDGET: REQUIRES EXPLICIT USER AUTHORIZATION /
CONCURRENT_SHADOW_RUNS: UNSUPPORTED_BY_DESIGN /
PUBLIC_RESTORE_FAILURE_ATOMICITY: UNVERIFIED /
HISTORICAL_PYCACHE_INTEGRITY_EVENTS: FAIL (2) /
HISTORICAL STATE BACKFILL: BLOCKED /
REAL DATABASE MIGRATION: NOT STARTED /
PRODUCTION NETWORK INGESTION + WORKER + SYSTEMD + API + FRONTEND: NOT STARTED

## Offline design contract

The only implemented acquisition transport is dependency-injected
`FakeTransport`. An explicit single-provider/competition/season/operation
configuration is signed into a durable `/tmp` session. A private SQLite ledger
commits request intent and `DISPATCH_STARTED` before every possible transport
attempt; each retry consumes another durable budget unit. Budget exhaustion
and operation-scope failures stop before transport. A possible dispatch with
no durable response receipt becomes `OUTCOME_UNKNOWN` and cannot retry
automatically.

Accepted bytes use a fixed response limit, exclusive mode-`0600` staging,
file fsync, atomic rename, parent-directory fsync, and exact SHA/size binding.
The implementation then reuses the existing same-FD strict JSON gate, shared
pagination inspector, exact status matrix, and durable offline shadow
ingestion; it does not copy those contracts. Business state can be applied
only through a `PreparedTrialCopy` temporary database.

Real subprocess tests cover intent-before-transport, possible dispatch before
receipt, response before rename, rename before ledger, validation before
apply, state commit before manifest, and feature commit before completion.
The acquisition `flock` rejects a second process before transport.

The Allsvenskan Phase-0 audit found repository code/configuration and isolated
2026-07-19/21 execution evidence, but no configured/running scheduler on this
host and no real odds snapshot for 2026-07-25/26. Provider/competition ID 67
could not be independently re-proved from surviving raw/DB evidence and is
`UNVERIFIED IN THIS REVIEW`.

Closing evidence and the complete boundary are in
`docs/audits/single-competition-live-shadow-design.md`. Offline design
validation grants no real network authority and is not live ingestion
validation.

---

# Validated Prerequisite: Shadow artifact trust and durable recovery closure

## Status

SHADOW_ARTIFACT_TRUST_BOUNDARY_VALIDATED /
DURABLE_CROSS_PROCESS_SHADOW_RECOVERY_VALIDATED /
READY_FOR_FINAL_SHADOW_RE_REVIEW /
CURRENT_SHADOW_INGESTION_INTEGRITY: PASS /
HISTORICAL_PYCACHE_INTEGRITY_EVENTS: FAIL (2) /
PUBLIC_RESTORE_FAILURE_ATOMICITY: UNVERIFIED /
HISTORICAL STATE BACKFILL: BLOCKED /
REAL DATABASE MIGRATION: NOT STARTED /
PRODUCTION NETWORK INGESTION + WORKER + SYSTEMD + API + FRONTEND: NOT STARTED

## Historical independent findings retained

The first offline-shadow implementation and its same-process test suite were
independently overturned with **P0=0 / P1=5 / P2=2 / `FIX_REQUIRED`**:

1. default JSON parsing accepted duplicate keys with last-write-wins;
2. pagination/completeness was accepted from caller evidence rather than
   independently recomputed from raw provider structure;
3. status/started/finished/cancelled combinations lacked an exact matrix;
4. a forged `COMPLETED` manifest could return a forged result without DB truth;
5. recovery depended on creator-PID/process-memory state and was not durable;
6. artifact regular-file/type/size constraints were incomplete;
7. concurrent runs on the same temporary DB had no cross-process exclusion.

Those findings remain part of the audit history; the old validation labels are
not evidence for this closure.

## Closure contract

The formal offline chain now starts from the sanitized raw-provider projection,
not the canonical derivative:

```text
immutable raw-provider projection
→ strict SHA-before-parse artifact gate
→ shared raw pagination inspector
→ exact status/flag normalization
→ atomic identity/snapshot/observation apply
→ DB-derived current/as-of and feature truth
→ signed manifest hint + durable signed session descriptor
→ cross-process crash recovery under an exclusive session lock
```

- Artifact reads use one `O_NOFOLLOW` file descriptor and the same immutable
  bytes for SHA-256 and strict JSON parsing. Any-depth duplicate keys,
  NaN/Infinity, BOM, invalid UTF-8, trailing data, non-object top levels, and
  excess structure depth fail with a fixed safe exception. Files must be
  current-UID regular single-link files, not group/world writable, and no
  larger than the fixed 16 MiB limit.
- The reviewed raw projection is
  `tests/fixtures/fotmob/cwc_2025_competition_schedule_raw.json`, SHA-256
  `b2852c04cdcfd812a92164e482309cdca634c3a38dca4973adcf94a3ebbc67fc`.
  Its recorded saved-source SHA-256 is
  `6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d`.
  The canonical fixture remains unchanged and is no longer sufficient by
  itself to attest raw pagination completeness.
- `backend/schedules/pagination.py` is the single pure implementation imported
  by both the competition pilot and shadow normalizer. Every detected,
  unresolved, collided, orphaned, malformed, or conflicting known pagination
  marker fails closed even if the envelope claims `NOT_DETECTED`.
- The supported FotMob matrix is exact: `NS=(false,false,false)`,
  `FT/AET/Pen=(true,true,false)`, and `Can=(false,false,true)`. Values must be
  real booleans. Unknown and unsupported live statuses fail closed.
- A manifest is a signed recovery hint, never database fact. Every invocation,
  including a `COMPLETED` replay, validates immutable identity and classifies
  exact DB content as `NO_STATE`, `STATE_COMPLETE`, `FEATURES_COMPLETE`, or
  `PARTIAL_OR_CONFLICTING`. Returned completion summaries are rebuilt from
  verified identities, snapshots, event, associations, features, and ordered
  lineage inputs. Insert/skip deltas remain manifest audit fields only.
- Only declared forward phase transitions are legal:
  `NEW → ARTIFACT_VALIDATED → STATE_APPLIED → FEATURES_APPLIED → COMPLETED`.
  `FAILED` is reachable only from a non-completed phase and retry resumes from
  the recorded last successful phase. Manifest-ahead and partial/conflicting
  DB states fail closed; DB-ahead commit boundaries reconcile forward.
- The durable descriptor is signed by a random 256-bit capability in a private
  `0700` workspace. Reopen does not depend on creator PID or `_SESSIONS`, and
  revalidates path bindings, owner/mode/link count, complete basename companion
  pathsets, zero-byte-WAL boundary, recovery/source/main fingerprints,
  migration ledger/schema, signed manifests, and workspace pathset.
- `flock(LOCK_EX|LOCK_NB)` rejects every overlapping process/run on the same
  session/DB with a fixed error. `CONCURRENT_SHADOW_RUNS:
  UNSUPPORTED_BY_DESIGN`; crash recovery is not described as concurrency-safe.
- Manifests use strict JSON, mode `0600`, an exclusive mode-`0600` temp file,
  file fsync, atomic replace, and parent-directory fsync. Symlink, hardlink,
  duplicate-key, corrupt, unknown, or unsigned control files fail closed.

The already-validated atomic batch, exact/later/out-of-order observation,
absence, changed-state versioning, point-in-time lineage, feature retry, CWC
three-run business results, and network hard block remain mandatory regression
gates. This remains offline tooling only.

Final command counts are recorded from the closing executions in
`docs/audits/offline-schedule-shadow-ingestion-v1.md`. The closure ended with
**P0=0 / P1=0** and current-round integrity **PASS**. The two historical cache
events remain failures and were not deleted, touched, or relabelled.

---

# Validated Prerequisite: Temporary production-DB copy migration/import trial

## Status

TEMP_COPY_TRIAL_SAFETY_VALIDATED /
DESTINATION_SQLITE_SIDECAR_BINDING_VALIDATED /
UNKNOWN_SQLITE_COMPANION_PATHSET_VALIDATED /
HISTORICAL_IDENTITY_CROSS_RUN_IDEMPOTENCY_VALIDATED /
CWC_OFFLINE_FORMAL_SCHEMA_IMPORT_VALIDATED /
HISTORICAL ROUND INTEGRITY: FAIL (NEW REPOSITORY PYCACHE) /
HISTORICAL SIDECAR-CLOSURE ROUND INTEGRITY: PASS /
CURRENT UNKNOWN-COMPANION ROUND INTEGRITY: FAIL (NEW REPOSITORY PYCACHE) /
READY_FOR_FINAL_UNKNOWN_SIDECAR_REVIEW: NO (INTEGRITY) /
REAL DATABASE MIGRATION: NOT STARTED /
PRODUCTION INGESTION + WORKER + API + FRONTEND: NOT STARTED

## Goal and locked boundary

Use one ordinary-file exact copy of real `data/allwin.db` under a new `/tmp`
directory to exercise the current formal 0001→0002→0003 migration, legacy
content preservation, failure/recovery, identity-only backfill, and the
validated CWC canonical fixture. The real source and its WAL/SHM remain
read-only; the trial performs no live request and grants no production
integration authority.

## Validated evidence

- Source WAL was 0 bytes and source SHA/size/mtime/inode stayed stable across
  the copy. The pre-migration mode 0600 copy exactly matched source SHA
  `92a6a39c40dfb21f9dacfe6a8e8953f6b0a971ebb5b40a6ae9f253ad00ab364e`,
  had `integrity_check=ok`, ledger 1 only, and 11,115 `dim_match` rows.
- The initial temporary-copy report was independently rejected with
  **P0=0 / P1=3 / P2=2 / `FIX_REQUIRED`**. The old public helper accepted an
  arbitrary temporary path, did not durably bind source/destination identity,
  and the identity replay proof reused the same `created_at`. Provider and
  provider-match identifiers also lacked a complete canonical input contract.
  This history is retained; the old same-T0 result is not presented as
  cross-run idempotency.
- The replacement workflow is
  `prepare_trial_copy(source) -> PreparedTrialCopy ->
  migrate_prepared_trial_copy(handle)`. It creates a fresh current-user mode
  `0700` run directory below the resolved system temp root and uses
  exclusive/no-follow creation for mode `0600`, single-link destination and
  recovery files. A raw destination path is not a migration input.
- The prepared handle binds process/UID, run-directory device/inode, source
  main/WAL/SHM fingerprints, destination/recovery device/inode/SHA, and the
  complete recognized destination/recovery `-wal`/`-shm`/`-journal` set.
  Every present sidecar must be a current-user mode `0600`, single-link
  regular file; the WAL must be zero bytes and a rollback journal must be
  absent. The bound set and fingerprints are rechecked before and after the
  read-only integrity probe. A bound zero-byte WAL and private SHM remain a
  valid SQLite read-only state; an introduced, replaced, linked, mutated, or
  non-quiescent sidecar fails closed before migration.
- The current formal runner applied exactly 0002 and 0003. Ledger identity,
  all 42 reviewed schedule objects, integrity, and foreign keys passed.
  Deterministic digests over every original column in all 18 legacy tables
  were unchanged. A second runner call applied 0 and did not change DB SHA,
  size, or mtime.
- A temporary faulting 0003 proved transaction rollback: 0002 remained
  committed, while the 0003 ledger row, every schedule object, and the fault
  object remained absent. File recovery from an untouched exact image restored
  the original SHA/ledger and allowed a fresh formal 0002+0003 application.
- Repository ingestion provenance plus the real-copy audit permit all 11,115
  `Match_ID` values to become stable FotMob identities. They are all non-null,
  unique, positive integers with complete legacy competition/team references.
  `record_match_identity()` was added as the minimal formal-service capability
  needed to persist identity without inventing state.
- `created_at` is first-write provenance, not identity equality. A fresh
  exact-copy run inserted all 11,115 identities at T0; after closing and
  reopening the database, a T0+1 day replay skipped all 11,115 with zero
  conflicts and retained only the original T0 values. Provider case/space and
  zero-padded numeric-ID forms normalized to the same rows; a changed canonical
  binding still conflicted.
- Providers are NFKC-normalized, trimmed lowercase ASCII slugs
  (`[a-z][a-z0-9_-]*`, maximum 32). Provider match IDs accept only positive
  non-bool integers or bounded supported ASCII strings; numeric strings are
  canonicalized without leading zeroes. The service rejects arbitrary object
  stringification, and migration `0003` independently constrains direct SQL.
- Historical state backfill remains blocked: exact kickoff count is 0, legacy
  `Date` is date-only, status does not prove finished/cancelled semantics, and
  there is no trustworthy observation time. No historical state snapshot was
  created.
- The validated CWC fixture inserted 66 identities, 66 state snapshots, one
  event with 66 associations, and 126 finalized rest features. Exact replay
  skipped; a later same-content synthetic observation added only one event and
  66 associations. Business evidence remained 66/63/3, 132 home/away source
  relationships, Manchester City `NULL/105/90/102`, AET preserved, and
  cancelled excluded from feature lineage.
- The fixture contains no trustworthy observation timestamp. The trial uses a
  clearly labeled `trial_synthetic_observation` and leaves
  `source_updated_at=NULL`; it does not claim a new live/source validation.
- All trial database/artifact files are mode 0600 under `/tmp`. Production
  ingestion, Worker/systemd, API/frontend, and real database migration remain
  **NOT STARTED**.
- The revised `0003_schedule_state_v1.sql` SHA-256 is
  `7e69e2a15b469ed9286345c0e21ebe49efdeb09561b2e2075bc0222dce050b57`.
  The exact-copy, rollback/recovery, legacy digest, CWC import, and migration
  manifest evidence was rerun against these bytes.
- Round-local Git status/untracked sets, real databases and sidecars,
  canonical fixture, six historical artifacts, and `.pytest_cache` remained
  unchanged. However, the early RED run created one new repository
  `__pycache__` directory and two `.pyc` files under
  `analysis/cwc_production_integration_design/` after the baseline. They were
  not deleted or disguised as restored. The code gates are validated, but
  this round's integrity is **FAIL** and final independent re-review readiness
  is **NO** until the user decides how to handle those generated cache files.

## Destination SQLite sidecar binding closure

A later independent read-only review showed that the earlier “exact-copy
migration validated” wording was too broad. `PreparedTrialCopy` bound the
destination main-file SHA/inode/mode/link count but not its SQLite sidecars.
With auto-checkpoint disabled, a committed destination WAL mutation changed
the logical `dim_match` value while leaving the destination main-file
SHA/inode/mode/link count unchanged; the old public API accepted the handle
and began migration. The review classified this as **P0=0 / P1=1 / P2=2 /
`FIX_REQUIRED`**. The P2 findings were the absent permanent destination-WAL
counterexample and documentation that overstated the old proof. This history
is retained and the old exact-copy statement is superseded, not silently
rewritten.

Permanent tests were added before implementation. The old implementation
produced a real **28 collected / 9 selected / 9 failed / 19 deselected**
RED, with 0 skipped, xfailed, or warnings. The failures covered a committed
WAL mutation, zero-byte late WAL, replacement SHM, rollback journal, sidecar
symlink/hardlink, recovery-image WAL, a sidecar introduced after the final
read-only integrity operation, and a non-empty WAL left by migration.

The narrow closure adds destination and recovery
`SQLiteSidecarSetFingerprint` values to the prepared handle. Preparation
captures the recognized sidecar set only after successful read-only integrity
checks; validation requires the same set, inode, owner, mode, link count,
size, and content before migration. SQLite may advance only SHM mtime while
taking read locks, so the post-integrity comparison continues to bind every
other SHM attribute and content, followed by an exact final recheck. A
non-empty WAL or any rollback journal fails closed. The public API also checks
for a safe quiescent destination sidecar state before returning success and
reconfirms that the recovery image and its sidecars stayed unchanged.

Current `-W default` evidence:

- sidecar closure: **29 collected / 10 selected / 10 passed /
  19 deselected**;
- trial full file: **29 collected / 29 passed**;
- schedule-state schema: **120 collected / 120 passed**;
- migrations + contract: **50 collected / 50 passed**;
- CWC prototype: **76 collected / 76 passed**.

Every command above had 0 failed, skipped, xfailed, and warnings. No real
database, source WAL/SHM, production migration, endpoint, Worker, API, or
frontend path was used. The prior cache-producing round remains permanently
**Integrity=FAIL**; this closure uses the current repository state as a new
baseline and does not delete, touch, or disguise those historical paths.
Opening and closing branch/HEAD/tag/stash, status/untracked sets, four real
databases and existing WAL/SHM, canonical fixture, six historical artifacts,
108 `__pycache__` directories, 810 `.pyc` files, nine `.pytest_cache` paths,
and both 41,822 full / 40,524 `.git`-excluded pathsets matched exactly.
Current sidecar-closure integrity is **PASS**; it does not revise the earlier
round's **FAIL**.

Full evidence:
`docs/audits/schedule-state-temp-copy-migration-trial.md`.

## Unknown SQLite companion pathset closure

A final independent review retained the known WAL/SHM gate but found another
historical **P0=0 / P1=1 / P2=2 / `FIX_REQUIRED`** boundary. The fixed
`-wal`/`-shm`/`-journal` enumeration ignored other entries sharing the
destination or recovery basename, so `trial.db-wal2`,
`trial.db-mj ABCDEF12`, `trial.db-journal.extra`, arbitrary suffixes, and
prefixed non-regular entries could reach the migration runner. Permanent
unknown-companion tests and accurate lifecycle/restore documentation were
missing. This history is retained; it does not reopen identity,
normalization, 0003, legacy, or CWC findings.

Tests were added before implementation. The old implementation produced a
real **55 collected / 26 selected / 24 failed / 2 passed / 29 deselected**
RED with 0 skipped, xfailed, or warnings. Both destination and recovery
matrices covered ordinary arbitrary names, super-journal-shaped names,
extension suffixes, symlink, hardlink, directory, and FIFO entries. The same
RED also proved an unknown companion appearing after the read-only integrity
check or remaining after runner work was ignored; the two passing controls
were existing recovery journal/SHM drift rejections.

`PreparedTrialCopy` now binds the complete allowed companion-name pathset
separately for `trial.db` and `recovery.db`, in addition to the full known
sidecar fingerprints. The tool scans every workspace entry whose name starts
with the relevant basename. Only the exact `-wal`, `-shm`, and `-journal`
names can proceed to known-sidecar validation; every other name is rejected
as `UNBOUND_COMPANION` before its content is read or a symlink is followed.
Unknown entries are never deleted, truncated, renamed, or automatically added
to the allowlist. The final pre-open scan has no caller callback or wait
point. After SQLite closes the first migration connection set, the exact
known pathset is rebound before the internal no-op application, then rebound
again before public success; recovery remains independently unchanged.

The committed-WAL permanent evidence holds its writer open and proves runner
calls 0, identical WAL device/inode/owner/mode/nlink/size/SHA/mtime, identical
destination main identity, ledger version 1 only, zero schedule objects, and
the unconsumed logical mutation. Legitimate bound zero-byte WAL/private SHM
still passes. Observed lifecycle evidence remains: pre-migration WAL 0 and
SHM 32768 bytes, migration-time WAL peak approximately 181312 bytes,
post-close WAL 0 with no journal, possible SQLite WAL/SHM inode recreation,
main ledger 1/2/3 plus schedule schema, `integrity_check=ok`, zero FK findings,
and internal second application `applied=0`. Inode stability is not claimed;
tool ownership, committed main content, quiescent return, and final pathset
rebinding are the contract.

Recovery evidence is limited to immutable image/pathset checks, the historical
manual `/tmp` file replacement rehearsal, and clean-image remigration. No
public restore API exists, therefore
`PUBLIC_RESTORE_FAILURE_ATOMICITY: UNVERIFIED`. This is a later real-migration
runbook blocker and is not presented as passed.

Current `-W default` behavioral evidence:

- unknown companion: **55 collected / 26 selected / 26 passed /
  29 deselected**;
- committed WAL: **55 collected / 1 selected / 1 passed / 54 deselected**;
- known sidecar/companion regression: **55 collected / 34 selected /
  34 passed / 21 deselected**;
- trial full file: **55 collected / 55 passed**;
- schedule-state schema: **120 collected / 120 passed**;
- migrations + contract: **50 collected / 50 passed**;
- CWC prototype: **76 collected / 76 passed**.

All reported pytest commands had 0 failed, skipped, xfailed, and warnings.
The behavior closure is offline and changes neither schema SQL nor migration
business logic. Production integration, real migration, ingestion, Worker,
systemd, API, frontend, and provider requests remain **NOT STARTED**.

Current task integrity is separately **FAIL**. A task-local direct
`py_compile` invocation incorrectly created
`analysis/schedule_state_migration_trial/__pycache__/` and two `.pyc` files,
changing the new baseline counts from 108/810 to 109/812. Those paths were
not deleted, touched again, or disguised as restored. Full path counts changed
from 41,834 to 41,837 and `.git`-excluded counts from 40,524 to 40,527 only
for that directory and those two files; every pre-existing cache entry's
content and metadata remained identical. Git status/untracked sets, four real
databases and existing WAL/SHM, canonical fixture, six historical artifacts,
and `.pytest_cache` matched the opening baseline. The earlier independent
cache failure also remains permanently **FAIL**; neither event is rewritten
by the behavioral result. Consequently the implementation is ready in
behavioral terms but `READY_FOR_FINAL_UNKNOWN_SIDECAR_REVIEW` is **NO** for
this execution's integrity.

---

# Completed Module: Production schedule-state schema v1 direct-SQL closure

## Status

PRODUCTION_SCHEDULE_STATE_SCHEMA_V1_DIRECT_SQL_VALIDATED /
READY_FOR_FINAL_INDEPENDENT_SCHEMA_RE_REVIEW /
PRODUCTION INGESTION: NOT STARTED / WORKER: NOT STARTED /
API + FRONTEND: NOT STARTED / REAL DATABASE MIGRATION: NOT STARTED

## Goal

Formally separate stable match identity, append-only mutable schedule state,
observation evidence, deterministic current/as-of projection, and versioned
rest-feature lineage in core SQLite. Prove migration, rollback, transitions,
point-in-time behavior, and CWC semantic compatibility using only newly
created temporary databases.

## Locked scope

- Core migration `0003_schedule_state_v1.sql` adds new objects and coexists
  with legacy `dim_match`; it does not alter, update, rebuild, backfill, or
  replace that table.
- `schedule_match_identity` contains immutable provider identity and optional
  bridge to canonical `dim_match.Match_ID`, but no mutable match state.
- `schedule_match_state_snapshot` appends business-state versions. Identical
  content reuses a snapshot; NS→FT, postponement, reschedule, cancellation,
  recovery, TBD correction, and round/stage correction never overwrite
  history.
- `schedule_observation_event` stores immutable provider/scope/run evidence;
  `schedule_match_observation` is the immutable match/snapshot association.
  One event may associate multiple matches. Exact replay skips, later/earlier
  evidence appends, and same-time conflicting evidence rolls back.
- `current_schedule_match_state` and the as-of query order by observation
  event time, then observation ID as a declared stable defensive tie-breaker.
  Ingestion wall clock and source update time do not select current state.
- Rest-feature persistence is a three-step transaction:
  `schedule_rest_lineage_set` → ordered
  `schedule_rest_lineage_input` → finalized `schedule_rest_feature`.
  Incomplete lineage is not consumable. The compatibility
  `schedule_rest_feature_input` view exposes only finalized inputs.
- Every ordering/evidence timestamp is stored as fixed-width
  `YYYY-MM-DDTHH:MM:SS.ffffffZ`; Python canonicalizes valid UTC input and the
  database rejects non-canonical direct SQL.
- Migration identity is exactly `(version, filename, checksum)`. The manifest
  must be non-empty and continuous from version 1 before any database write.
- Every append-only table has INSERT conflict guards in addition to
  UPDATE/DELETE guards, so `INSERT OR REPLACE` remains blocked even if a raw
  caller disables `recursive_triggers`. `NOT EXISTS` reference guards keep
  business relationships fail closed even if a raw caller disables foreign
  keys.
- `dim_match_xref`, `dim_team_xref`, and `poll_state` remain in odds.db and
  retain their existing semantics. Existing `dim_match`, feature, model,
  Worker, API, systemd, and frontend paths are not integrated in this module.
- All migration/state probes use new `/tmp` or pytest temp SQLite. No real
  database row is migrated or written.

## Independent adversarial finding and closure history

The first offline design status was independently overturned by five P1 and
one P2 findings:

- a manifest containing `0001` and `0003` was accepted;
- variable-width/non-canonical timestamp text could misorder current/as-of;
- `INSERT OR REPLACE` bypassed append-only triggers when recursive triggers
  were off;
- FK-off raw connections could insert business orphans because scalar
  comparisons evaluated to SQL `NULL`;
- a feature header could commit before its declared lineage was complete;
- ledger filename provenance was not checked when version/checksum matched.

Permanent tests were added before the implementation change. The old code
produced **5/5 failed manifest cases** and **17/17 failed direct-SQL cases**.
This history remains part of the closure and is not rewritten as if the first
design had been sufficient.

## Acceptance scope

- formal migration/schema target;
- state transition and observation ledger target;
- current/as-of and lineage target;
- existing migrations and contract;
- validated CWC prototype;
- sealed CWC pilot;
- team + competition schedule pilots;
- `/tmp` compileall and `git diff --check`;
- start/end Git, real DB/WAL/SHM, fixture, artifact, cache, and worktree
  integrity comparison.

The full data model and operational boundary are documented in
`docs/architecture/production-schedule-state-schema.md`; implementation
evidence is in
`docs/audits/production-schedule-state-schema-v1.md`.

---

# Completed Module: Club World Cup offline production-integration design

## Status

PROTOTYPE VALIDATED / OFFLINE DESIGN + TEMP-DB PROOF IMPLEMENTED /
LATER-OBSERVATION IDEMPOTENCY CLOSURE IMPLEMENTED /
POINT-IN-TIME FEATURE LINEAGE CLOSURE IMPLEMENTED /
DETERMINISTIC FEATURE INPUT CONTRACT CLOSURE IMPLEMENTED /
SAFE TIMESTAMP ERROR BOUNDARY CLOSURE IMPLEMENTED /
INDEPENDENT RE-REVIEW COMPLETE / PRODUCTION INTEGRATION: NOT STARTED

## Goal

Use only the permanent, trimmed fixture derived from the validated saved
competition `78` response to prove a production-shaped calendar/team/rest
design in a caller-supplied temporary SQLite database. This module is not a
production ingestion path: it performs no HTTP request, creates no formal
migration, registers no Worker job, and never writes `data/*.db`.

## Validated design scope

- Registry natural key:
  `(provider, competition_id, requested_season)`.
- Calendar natural key: `(provider, provider_match_id)`.
- Team-match natural key:
  `(provider, provider_match_id, team_id)`.
- Rest-feature natural key:
  `(provider, provider_match_id, team_id, feature_version, input_set_hash)`.
- Observation natural key:
  `(provider, competition_id, requested_season, observed_at)`.
- All five tables are temporary proof tables with a `prototype_` prefix.
- Registry, calendar, team-match, and rest-feature rows contain stable business
  content only. Wall-clock `observed_at` is stored only in
  `prototype_schedule_observation`; later observations of identical content
  append one event without rewriting the original business rows. An exact
  event replay skips, and an earlier event time is allowed as an unambiguous
  append-only event.
- The CWC season strategy is explicitly `calendar_year` / `2025`; the pure
  validator separately supports adjacent `split_year` labels and exact
  configured labels, but never guesses from a competition name.
- All 66 saved source fixtures are retained: 63 non-cancelled and 3 cancelled.
  Every fixture produces two team relations (132 total). Cancelled, unfinished,
  friendly, unknown-class, or non-exact-kickoff rows are excluded from
  observed load.
- Competitive classification is an explicit verified-registry policy:
  `league`, `domestic_cup`, `continental`, `super_cup`, and
  `international_club` are eligible classes only after registry validation;
  `friendly`, `unknown`, `other`, and unverified registry rows are not.
- The 63 eligible fixtures produce 126 team rest-feature rows. Manchester City
  (`8456`) has matches `4685744`, `4685746`, `4685748`, `4685772` and computed
  kickoff-to-kickoff gaps `NULL`, `105`, `90`, `102` hours. The final `AET`
  status is retained; no speculative 30-minute subtraction is applied.
- One transaction covers registry, calendar, team relations, rest features,
  and the observation event. All existing business keys are preflighted before
  any insert. Exact replay skips; later identical content inserts only the
  observation. Any existing business key with different content raises an
  explicit conflict and rolls back the whole batch, including the new
  observation. There is no `INSERT OR REPLACE`, silent update, or
  last-write-wins path.
- Each rest feature derives `input_set_hash` from that team's ordered eligible
  prefix through the current match, `timeline[:i+1]`. Runtime
  `observed_at`/`computed_at`, temporary paths, and every later fixture are
  absent. A changed eligible input can affect only its current and downstream
  feature lineage; it cannot change an earlier feature hash. The helper itself
  enforces a non-empty prefix, unique Match IDs, and strictly increasing
  kickoff times. Reordered input, adjacent or non-adjacent duplicate IDs,
  equal kickoff times, and a fixture later than the final current match all
  fail closed. Callers cannot rely on silent sorting, deduplication, or Match
  ID tie-breaking.
- `prototype_match_calendar` proves only immutable import of this one completed
  historical response. It is not a production polling table and must not be
  copied as production DDL: normal kickoff/status/team corrections need
  append-only, per-match source-state snapshots separate from stable match
  identity.
- Existing partial, drifted, constraint-weakened, or mixed-purpose prototype
  schemas fail before any write. The gate compares the complete normalized
  DDL, not only column names and primary keys. The runner accepts only `/tmp`
  or pytest temporary paths and rejects repository `data/` before opening
  SQLite.
- `_parse_utc()` applies one fixed prototype-local error boundary to malformed,
  non-string, naive, and non-UTC timestamps. It never includes the input or
  field label in the final message and raises only after the parser's
  `except` scope has ended, so the covered paths expose neither a lower-level
  cause nor context.

## Permanent fixture

`tests/fixtures/fotmob/cwc_2025_competition_schedule_canonical.json` was
deterministically trimmed only after checking the saved source SHA-256
`6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d`.
It contains all 66 fixtures and only public identity, match, team, round,
status, and kickoff fields plus provenance. Its permanent byte SHA-256 is
`020b6eabecd9ca004611863d3b3f5820ee12ea2f1ff8f203f51e73a1bbf276b1`.
Permanent tests do not depend on the `/tmp` live artifact remaining present.

## Observation/idempotency closure

The prior `DESIGN_VALIDATED_WITH_TEMP_DB` claim covered only an identical
`observed_at`. Independent replay with the same fixture at
`2026-07-25T12:05:00Z` after `12:00:00Z` raised
`PrototypeConflictError` in `prototype_competition_registry`; that earlier
claim is therefore superseded for later-observation idempotency.

Permanent tests were added first. The old implementation produced a real RED:
**51 collected / 15 failed / 36 deselected**, exposing runtime-time content
drift, the missing observation table, the missing rest input-set hash, and the
missing explicit class policy. The implementation now separates stable
business entities from append-only observations. No production code,
migration, Worker, or network capability was added.

## Point-in-time feature-lineage closure

An independent canonical-fixture counterexample changed only final Manchester
City match `4685772` from `2025-07-01T01:00:00Z` to `02:00:00Z`. The first
feature `4685744` retained every actual feature value, but the old team-wide
timeline hash changed both its `input_set_hash` and `payload_hash`. The
permanent counterexample produced a real pre-fix RED:
**1 collected / 1 failed / 0 skipped / 0 xfailed / 0 warnings**.

`build_feature_input_set_hash()` now accepts only the stable timeline prefix
through the current feature and explicitly rejects a row later than the
current kickoff. Permanent propagation tests prove that a final-match change
leaves the first three Manchester City hashes unchanged, a second-match change
affects only the second and later features, and finished/cancelled transitions
cannot change earlier lineage. Observation-time-only reruns remain business
content no-ops and append only the observation event.

## Deterministic feature-input contract closure

The final independent adversarial review retained the canonical point-in-time
propagation results but returned **P0=0 / P1=1 / P2=1 / `FIX_REQUIRED`**.
Direct helper probes showed that an all-historical reordered prefix produced a
different valid hash, adjacent and non-adjacent duplicate Match IDs were not
reliably identified, and two different matches with the same kickoff were
accepted. The P2 was the missing explicit helper-level contract and permanent
coverage. This history remains part of the record.

The closure added permanent tests before changing the helper. The old
implementation produced a real deterministic-contract RED:
**61 collected / 6 selected / 2 passed / 4 failed / 55 deselected**, with
0 skipped/xfail/warnings. `build_feature_input_set_hash()` now independently
rejects duplicate Match IDs and any non-strict kickoff sequence before hashing;
the existing future-match and empty-prefix gates remain. It never silently
sorts, deduplicates, drops rows, or uses runtime time to repair input. The
canonical parser's own ordering is therefore no longer the only defense.

Final deterministic-contract validation:

- helper target including the retained future gate:
  **61 collected / 7 selected / 7 passed / 54 deselected**;
- prototype full file: **61 collected / 61 passed**;
- sealed CWC pilot: **41 collected / 41 passed**;
- team + competition pilots: **286 collected / 286 passed**;
- migrations + backend contract: **44 collected / 44 passed**, with 1
  existing `StarletteDeprecationWarning`.

Every command had 0 failed/skipped/xfailed. The first four had 0 warnings.
An independent canonical probe compared all 126 legal timeline-prefix hashes
against the pre-contract stable-input algorithm and found every hash unchanged;
the 66/132/63/3/126 business counts and Manchester City
`NULL / 105 / 90 / 102` gaps were also unchanged. `/tmp`-isolated compileall
and `git diff --check` both exited 0.

This is still an offline analysis prototype. It adds no production schema,
migration, Worker, systemd, API, frontend, live runner, network capability, or
real database write. Production integration remains **NOT STARTED**; the next
module remains formal design of append-only mutable per-match source-state
snapshots.

## Safe timestamp error-boundary closure

The final independent review after the deterministic-input closure retained
the lineage and observation results but found a new **P1 / `FIX_REQUIRED`**:
`_parse_utc()` used `raise PrototypeDataError(...) from exc`. A malformed
timestamp containing a synthetic path, credential-shaped URL, authorization
value, token, or body marker could therefore be recovered from the formatted
traceback and the accessible `__cause__` / `__context__`. The earlier broad
wording that prototype errors were already uniformly safe was premature and
is withdrawn; this closure is scoped only to the prototype timestamp call
paths that now have permanent coverage.

Permanent tests were added before the implementation change. The old function
produced a real RED: **76 collected / 15 selected / 1 passed / 14 failed /
61 deselected**, 0 skipped/xfail and 1 SeleniumBase startup
`PytestDeprecationWarning`. The matrix covers ordinary malformed values,
absolute paths, proxy credentials, Basic/Bearer, token and JSON/body shapes,
invalid timezone text, non-string values, Unicode/invalid-character input,
the lineage helper, and the complete canonical parser path.

`_parse_utc()` now returns the same values for legal `Z` and explicit
`+00:00` timestamps, continues to reject naive and non-UTC timestamps, and
raises the fixed `PrototypeDataError("invalid UTC timestamp")` outside the
lower parser's active `except`. Permanent assertions scan `str`, `repr`,
`args`, formatted traceback, cause, context, stdout, stderr, and captured logs;
all covered invalid inputs have `cause is None`, `context is None`, and
`suppress_context=True`. No raw timestamp, field label, URL, path, credential,
or lower parser text is emitted.

This does not claim every exception in the repository has been audited. It
does not change the lineage algorithm, observation ledger, canonical fixture,
production schema, migration, Worker, or any live path.

## Acceptance evidence

- Safe timestamp pre-fix RED:
  **76 collected / 15 selected / 1 passed / 14 failed / 61 deselected**.
- Safe timestamp target after the fix:
  **76 collected / 15 selected / 15 passed / 61 deselected**.
- Deterministic helper target including the retained future gate:
  **76 collected / 7 selected / 7 passed / 69 deselected**.
- Prototype permanent tests: **76 collected / 76 passed**.
- Sealed CWC pilot regression: **41 collected / 41 passed**, 0
  failed/skipped/xfailed.
- Team + competition schedule pilots: **286 collected / 286 passed**, 0
  failed/skipped/xfailed.
- Migration + backend contract regression:
  **44 collected / 44 passed**, 0 failed/skipped/xfailed.
- Each current pytest process emitted exactly 1 instance of the same
  SeleniumBase legacy-hook startup `PytestDeprecationWarning`; it is one
  third-party source repeated across independent processes, not multiple
  product defects. No current command emitted the historical Starlette
  warning. No warning filter was used.
- Every pytest run used `THORDATA_PROXY=http://offline.invalid:1`,
  `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `-W default`, and explicit
  socket/DNS/urllib/requests/curl_cffi blocking.
- An independent canonical probe matched all 126 legal `input_set_hash`
  values and reconfirmed 66 calendar / 132 team / 63 non-cancelled /
  3 cancelled / 126 rest features, Manchester City
  `NULL / 105 / 90 / 102`, AET preservation, and cancelled exclusion.

## Boundaries and next phase

- Historical data verdict remains
  `GO_SINGLE_COMPETITION_DATA_VALIDATED`.
- The one-use CWC live runner remains `PERMANENTLY_SEALED`; historical request
  budget remains 3/3 consumed.
- `pagination_status=NOT_DETECTED_FOR_SAVED_RESPONSE` describes only this
  saved response and does not assert that the endpoint never paginates.
- Observed historical load uses only finished eligible fixtures. A future
  projected schedule-gap feature must be separately named and marked
  projected; this prototype does not mix the two.
- Formal production design has four mandatory layers:
  1. `stable_match_identity`: provider, provider match ID, and competition
     identity only; mutable schedule state does not belong here.
  2. `append_only_match_state_snapshot`: kickoff, status, finished,
     cancelled, round, home/away including TBD corrections, per-match payload
     hash, and observed time. A source-state change appends a version and never
     overwrites history.
  3. `current_match_state_projection`: a deterministic projection over valid
     snapshots with explicit ordering, conflict handling, and same-event-time
     multi-version rules. It is not the historical truth table.
  4. `versioned_feature_lineage`: every as-of feature identifies the exact
     snapshot/input set used, so later source changes cannot contaminate early
     features and historical computation can be rebuilt by observation time.
- `prototype_schedule_observation` may remain as supplemental response-level
  evidence, but it cannot replace any of those four layers. Normal NS→FT,
  postponement, cancellation, or TBD team correction must neither overwrite
  history nor become a permanent immutable-key conflict, and a response-level
  hash alone cannot reconstruct the changed match. The complete four-layer
  design is a blocking condition for any formal migration.
- No production schema/migration, persistent poll/job state, Worker, systemd,
  API, frontend, registry rollout, batch, or live request is implemented.
- Before production work, promote the stable parsing/eligibility/rest
  functions into a single source under `backend/schedules/`, then have
  analysis tests call that source. Do not make production import
  `analysis.*`, and do not revive the sealed pilot runner.
- The next separately authorized phase is production schema/migration plus a
  disabled-by-default persistent job implementation, but only after the
  mutable per-match snapshot schema above is independently designed.
  Production readiness remains **NO**.

Full evidence:
`docs/audits/cwc-production-integration-design.md`.

---

# Completed Module: Club World Cup single-competition pilot

## Status

COMPLETE / DATA: GO_SINGLE_COMPETITION_DATA_VALIDATED /
RUNNER: PERMANENTLY_SEALED / 3 OF 3 LIVE REQUESTS CONSUMED /
NO PRODUCTION INTEGRATION

## Goal

Use one fixed Manchester City match date and no more than three actual FotMob
HTTP calls to discover and validate only the FIFA Club World Cup competition
response for season 2025. All raw responses and live-run artifacts stay under
`/tmp`; pagination following, batch collection, production databases,
registries, Worker integration, and deployment are out of scope.

## Request sequence

1. `daily_matches("20250619")` for unique competition discovery.
2. `league_matches(discovered_competition_id, "2025")` for the required
   identity/season/schema/pagination/cross-link gate.
3. Only if request 2 passes, the optional same-competition
   `league_matches(discovered_competition_id, "2023")` season comparison.

The sequence above is a historical record. All three authorized calls were
consumed. No fourth call is authorized.

## Permanent runner seal

The independent zero-network review found
`RECOVERY_REPLAY_GUARD_NOT_DURABLE`: a repeated recovery against an already
completed output directory created a fresh in-memory guard, entered
`league_matches(78, "2025")`, consumed one fake transport call, and only then
hit the existing raw file's `O_EXCL` failure.

This closure does not add a durable request ledger or turn the pilot into a
collector. `run_live()` and `resume_live_from_saved_daily()` now fail as
`LIVE_RUNNER_SEALED` before client construction, transport, DNS/proxy lookup,
output allocation, or artifact reads/writes. `execute_pilot()` remains only as
an injected-FakeClient offline fixture harness. Any future production
integration must use a separate module with persistent job/poll state.

## Result

- Discovered competition: `78` / `FIFA Club World Cup Grp. G` in the daily
  bucket; competition response name `FIFA Club World Cup`.
- Discovered match: `4685744`, Man City (`8456`) vs Wydad Casablanca
  (`102050`), `2025-06-18T16:00:00Z`.
- 2025 response: season `2025`, 66 unique fixtures, 63 finished/non-cancelled,
  3 source-declared cancelled León fixtures, 4 Manchester City fixtures,
  pagination `NOT_DETECTED`, daily cross-link passed.
- 2023 response: season `2023`, 7 unique fixtures, zero Match ID overlap with
  2025, pagination `NOT_DETECTED`; season verdict
  `SEASON_PARAMETER_EFFECTIVE`.
- The theoretical 63/4 reference aligns with finished/non-cancelled fixtures
  and Manchester City fixtures, not with the raw `allMatches` length of 66.
  No source record was deleted to force alignment.
- Historical bottom-level HTTP calls: 3/3 consumed; no fourth historical
  request and no pagination following.
- Permanent-seal RED: 4 failed / 37 deselected on the old runner.
- Permanent-seal targeted gate: 4 passed / 37 deselected; full pilot:
  41 passed; existing team + competition: 286 passed; FotMob status/decode/
  warning safety target: 25 passed. No failures, skips, xfails, or warnings.
- This closure performed 0 network requests.
- Raw responses and summaries exist only under
  `/tmp/allwin-cwc-single-pilot-20260725T100615Z/` with mode 0600.
- Full evidence: `docs/audits/club-world-cup-single-pilot.md`.

## Previous module closure

- `competition schedule fail-closed closure`: **COMPLETE**
- Independent verdict:
  **READY_FOR_CLUB_WORLD_CUP_SINGLE_PILOT**
- The historical implementation, RED evidence, test counts, warning
  disclosures, and integrity record remain below and are not rewritten.

---

# Completed Module: competition schedule fail-closed closure

## Status

COMPLETE — independent verdict:
READY_FOR_CLUB_WORLD_CUP_SINGLE_PILOT. This verdict permits only the separately
authorized single-competition pilot above, not scale-out.

**Note on internal contradiction below (added 2026-08-04, not part of the original entry):**
The "Completion status" section further down this entry (`Module status: OPEN /
READY FOR FINAL INDEPENDENT P2 RE-REVIEW`, `Ready for Club World Cup
single-competition pilot: NO`) appears to conflict with the `COMPLETE` verdict
above. Resolution: the CWC single pilot in fact ran and was sealed afterward
(see `Completed Module: Club World Cup single-competition pilot` later in this
file, and `docs/current-state.md` §14) — a P2 re-review gate was cleared by
that later, separately-authorized execution without a dedicated follow-up
entry being filed here. Treat this entry's own `NO` line as historical
(true at the time it was written), not as the current gate state.

## Goal

Close the confirmed competition schedule pilot fail-open paths before any Club World Cup probe or large-scale competition collection.

## Work items

### P0

- A required competition with valid identity and returned season but `fixtures.allMatches=[]` must fail as `EMPTY_FIXTURES`.
- Preserve the distinction between an empty competition response and a non-empty competition whose target-team fixture count is zero.
- On required failure, persist all registry results first, return exit 1, and make zero downstream calendar/team writes or rest calculations.

### P1

- Missing, empty, or invalid returned season must fail as `SEASON_UNVERIFIABLE`; a mismatched season must fail as `SEASON_MISMATCH`.
- Implement known-marker pagination inspection for the pilot:
  - Inspect only direct metadata under `raw["fixtures"]`; never recurse into `allMatches` or other business objects.
  - Detect known markers such as `hasMore`, `next`, `cursor`, and all three
    supported page dialects: `currentPage/totalPages`, `page/pageCount`, and
    `page/totalPages`.
  - Treat incomplete page families, malformed values, casefold collisions, and
    contradictory complete dialects as unresolved, with evidence naming only
    fields that are actually present plus the specific missing companion.
  - A complete dialect consumes only its own keys: absent optional aliases do
    not create false incomplete evidence, but any additional known page-family
    marker left outside every complete dialect remains an orphan and is
    `UNRESOLVED` regardless of whether its value is an integer, string,
    boolean, or negative number.
  - A collision affects only its own normalized key. It must not suppress an
    independent non-colliding orphan; a collided companion is present (though
    unusable), so it must not be falsely reported as missing.
- Keep every response boundary secret-safe:
  - Read and validate `response.status_code` inside the worker transport
    `try/except`, so an accessor failure follows the normal retry/redaction
    path.
  - Route all public JSON responses, SSR text, and `__NEXT_DATA__` JSON through
    shared decode helpers that expose only a fixed operation name and safe
    exception class.
  - Keep downstream `parse_season_player_stats()` warnings free of external
    stat names, URLs, response bodies, exception text, and credentials.
  - Preserve detected and unresolved evidence together.
  - Absence of known markers means only `NOT_DETECTED` for that response; it does not prove the endpoint never paginates.
  - Detected or unresolved pagination must fail closed.
  - Full pagination retrieval is out of scope for this module.
- Use `(competition_id, requested_season)` as the `pilot_competition_registry` identity.
- Explicitly reject incompatible old pilot schemas before any registry/calendar/team write.
- Enforce season verification inside the low-level competition parser.
- Bind legacy contract tests to the dynamic temporary core DB.
- Eliminate import-time proxy environment/dotenv reads from `FotMobClient`.
- Treat every case-insensitive collision among known direct pagination keys as
  `UNRESOLVED`, preserve every original field path, and stop before downstream
  work even when the colliding values are identical.
- Replace raw FotMob transport/HTTP failures with safe project exceptions that
  expose only an exception class name, HTTP status, and attempt count; never
  retain response bodies, URLs, external exception text, or the original
  exception chain.
- Derive completeness only after identity, season, schema, non-empty competition fixtures, and known-marker pagination checks pass.
- Connect `season_parameter_verified` to actual evidence or leave completeness explicitly unverified; it must not remain an unused success-adjacent field.

### P2

- Synchronize both schedule audit reports with the actual pytest collection and execution results from this work.
- Remove stale test-count and completion claims; do not pre-hardcode future counts.
- Ensure recommendations match the implemented fail-closed gate.
- Keep the Allsvenskan API fixture inside the seven-day display window by
  deriving one timezone-aware UTC kickoff at test time and reusing its date,
  season, and exact timestamp throughout the fixture.

## Affected files

- `analysis/competition_schedule_pilot/fotmob_competition_schedule_pilot.py`
- `analysis/competition_schedule_pilot/test_fotmob_competition_schedule_pilot.py`
- `analysis/team_schedule_pilot/test_fotmob_team_schedule_pilot.py`
- `backend/fotmob_client.py`
- `backend/providers/fotmob_snapshots.py`
- `tests/backend/conftest.py`
- `tests/backend/test_contract.py`
- `tests/backend/test_api_v1.py`
- `docs/audits/competition-schedule-pilot.md`
- `docs/audits/team-schedule-pilot.md`
- `docs/current-state.md`

No production database migration, production Worker integration, live request, or batch collector is in scope.

## Permanent acceptance tests

- `allMatches=[]` -> `EMPTY_FIXTURES`, exit 1, registry saved, downstream zero calls.
- `fixture_count>0` and `target_team_fixture_count=0` -> allowed to pass.
- Returned season missing, empty, or invalid -> `SEASON_UNVERIFIABLE`.
- Returned season mismatch -> `SEASON_MISMATCH`.
- `hasMore=true`, next URL, cursor, or `page<totalPages` -> pagination unresolved and fail closed.
- All three supported page dialects accept only non-boolean, non-negative
  integers; equal pages mean no continuation, a lower current page means
  detected continuation, and reversed/malformed pairs are unresolved.
- Incomplete page families identify the actual present field and its missing
  companion; multiple complete dialects must agree semantically or fail
  unresolved while retaining detected evidence.
- A complete page dialect must not mask an extra orphan known marker; the
  complete pair keeps its own detected/no-continuation outcome while the orphan
  records its actual path and exact missing companion. A complete dialect by
  itself, including consistent overlapping dialects, must not be misreported.
- Nested match business `next`/page fields, `QAData.cursor`, and `details.next` -> ignored.
- Detected + unresolved direct metadata -> both evidence sets retained and persisted.
- Explicitly empty URL/cursor markers -> no continuation; non-empty/invalid marker
  types and malformed page pairs -> unresolved.
- No known pagination marker, or explicitly false/empty markers -> `NOT_DETECTED` only.
- Any case-insensitive collision for a known direct pagination key -> `UNRESOLVED`,
  complete collision-path evidence persisted, exit 1, downstream zero calls.
- Old single-key registry schema -> structured exit 1, no traceback/downstream writes, old rows unchanged.
- Direct parser calls reject invalid/missing/mismatched season.
- `FotMobClient(proxy="")` and explicit proxy construction do not read environment or dotenv.
- FotMob transport exception text, proxy-auth shapes, HTTP response bodies,
  request URLs, and original exception chains are absent from logs and final
  exceptions.
- The Allsvenskan fixture computes kickoff as current UTC + three days, proves
  it is still in the future, and publishes successfully.
- Legacy invalid-season response is sourced from a sentinel in tmp core DB and does not refresh the real SHM.
- Same competition with two seasons -> two registry rows.
- Same competition and season with identical data -> idempotent skip.
- Same competition and season with changed key fields -> explicit conflict.
- Mixed successful/failed competitions -> all registry rows saved first and downstream zero calls.
- Existing required identity-failure gate -> no regression.

## Acceptance commands

Use no network, no repository caches, and no real database writes:

```bash
THORDATA_PROXY=http://offline.invalid:1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest -p no:cacheprovider --collect-only -q \
  analysis/team_schedule_pilot/test_fotmob_team_schedule_pilot.py \
  analysis/competition_schedule_pilot/test_fotmob_competition_schedule_pilot.py

THORDATA_PROXY=http://offline.invalid:1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest -p no:cacheprovider -q -rA \
  analysis/team_schedule_pilot/test_fotmob_team_schedule_pilot.py \
  analysis/competition_schedule_pilot/test_fotmob_competition_schedule_pilot.py

THORDATA_PROXY=http://offline.invalid:1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest -p no:cacheprovider -q \
  <relevant-regression-files>

PYTHONPYCACHEPREFIX="$(mktemp -d /tmp/allwin-page-dialect-compile.XXXXXX)" \
  .venv/bin/python -m compileall -q analysis backend tests/backend
git diff --check
```

Run the appropriate related regressions or full pytest only when they can be isolated from real databases. If a safe full run cannot be proven, state the reason and mark it `UNVERIFIED`; do not imply it ran.

Documentation test counts must come from this work's actual pytest collection, not from a number written in advance.

## Integrity acceptance

- Real `data/*.db` SHA-256, size, and mtime are identical before and after.
- No new WAL/SHM files or changes.
- No network access.
- Do not read or output proxy credentials.
- Do not generate `__pycache__` or `.pytest_cache`.
- The previous independent review accidentally refreshed three existing
  `.pyc` files. That historical review-integrity failure is not rewritten as a
  pass: this task neither deletes nor restores them and judges cache integrity
  only against the new baseline recorded at this task's start.
- No commit, push, tag, or deploy.
- Do not clean, stash, delete, or otherwise alter the existing dirty worktree.

## Completion status

- Module status: OPEN / READY FOR FINAL INDEPENDENT P2 RE-REVIEW
- P0 implementation: PASS
- P1 implementation: PASS
- P2 test-contract/documentation correction: PASS / AWAITING INDEPENDENT RE-REVIEW
- Fifth-round independent pre-fix verdict: `FIX_REQUIRED` for three P1s
  (collision masking orphan evidence, HTTP-200 decode leakage, unprotected
  `status_code` access) and two P2s (incorrect permanent assertions and stale
  documentation).
- Fifth-round RED before implementation: pagination collision+orphan
  **8 failed**; FotMob status/decode/warning redaction **25 failed**.
- Fifth-round post-fix targeted gates: pagination collision+orphan
  **8 passed**; full pagination/orphan/collision scope **61 passed**; FotMob
  status/decode/warning redaction **25 passed**. All had 0 failed,
  skipped, xfailed, and warnings.
- Final independent P2 pre-fix review: **P0=0 / P1=0 / P2=2 /
  `FIX_REQUIRED`**. The real local `curl_cffi.Response` safely produced
  `FotMobDecodeError ... UnicodeDecodeError`, while its permanent test
  incorrectly required the version-specific `JSONDecodeError`; the same review
  also found that the four closure documents incorrectly claimed 0 warnings.
- Final P2 correction changed no production code. The real-response test was
  reproduced as **1 failed** before the test-only change, then **1 passed** after
  asserting the stable contract (`FotMobDecodeError`, fixed `team_data`
  operation, identifier-only external class, no secret surfaces or exception
  chain) without binding curl_cffi's internal decoder choice.
- Third-round RED: pagination collision matrix 16 failed / 2 scope controls
  passed; FotMob redaction matrix 4 failed before implementation
- Fourth-round RED: page-dialect closure selection 21 failed / 6 existing
  controls passed before implementation
- Fourth-round narrow orphan-marker RED: 8 failed / 5 complete-dialect
  controls passed before implementation
- Competition pilot tests: 193 collected / 193 passed,
  0 failed/skipped/xfailed/warnings
- Team pilot tests: 93 collected / 93 passed,
  0 failed/skipped/xfailed/warnings
- Team + competition pilot collection/execution: 286 collected / 286 passed,
  0 failed/skipped/xfailed/warnings
- FotMob status/decode/downstream-warning target: 25 collected / 25 passed,
  0 failed/skipped/xfailed/warnings
- Allsvenskan API class: 7 collected / 7 passed, 1
  `StarletteDeprecationWarning`
- API/cache/studio full-file scope: 78 collected / 78 passed, 1
  `StarletteDeprecationWarning`
- Legacy DB isolation test: PASS
- `tests/backend/test_contract.py`: 30 collected / 30 passed, 1
  `StarletteDeprecationWarning`
- Historical ten-file FotMob/provider scope: 346 collected only, with 1
  `StarletteDeprecationWarning` during collection. The four items in
  `tests/backend/test_e2e_seed.py` were **NOT RUN / UNVERIFIED**.
- Safe nine-file FotMob/provider targeted collection/execution: 342 collected /
  342 passed; 0 failed/skipped/xfailed and 1
  `StarletteDeprecationWarning`
- Broad backend functional regression: 531 collected / 531 passed,
  0 failed/skipped/xfailed and 3 warnings; `test_e2e_seed.py` explicitly
  excluded. The warnings are the same single `StarletteDeprecationWarning`
  plus two test-resource `ResourceWarning`s: an unclosed `Popen(stdout=PIPE)`
  reader allocated in `TestProductionDisabledUvicornSmoke`, and an unclosed
  `HTTPServer` socket in `TestMarkerNotInjectable`.
- The Starlette/httpx warning is one third-party deprecation source repeated
  once in each independent pytest process, not multiple distinct defects.
  The two ResourceWarnings are existing test-cleanup issues outside this
  production-code-frozen P2 correction and remain recorded, not suppressed.
- Main database files: PASS (SHA-256/size/mtime unchanged)
- Strict WAL/SHM integrity: PASS (set/SHA-256/size/mtime unchanged)
- 2026-07-25 round-local cache snapshot: start/end both 107
  `__pycache__` directories and 808 `.pyc` files; content and metadata digests
  unchanged
- Ready for final independent P2 re-review: YES
- Ready for Club World Cup single-competition pilot: NO

## Unverified

- Live FotMob pagination behavior beyond previously captured evidence
- FIFA Club World Cup competition identity and schedule coverage
- Cross-season behavior against new live responses
- Multi-team request limits and collection safety
- Full all-competition completeness

## Entry conditions for the next module

Ready for Club World Cup single-competition pilot may become `YES` only after:

- Every P0/P1 permanent test passes through production functions and CLI behavior.
- The two-season registry test proves both rows are retained.
- There are zero unexplained skip/xfail results.
- An independent review confirms the original fail-open probes are closed.
- Database and dirty-worktree integrity evidence is unchanged.
- Audit reports and `docs/current-state.md` match the implementation.
- The user separately authorizes the Club World Cup probe.

Large-scale collection remains prohibited until separately reviewed and authorized.
# FIVE_CRITICAL_PRODUCT_FIXES_V1（2026-07-30）

产品范围冻结为五项：truthful freshness、immutable production preview、动态联赛
目录、统一球队显示、未来七天比赛发现。实现和永久测试位于：

- `backend/content_status.py`
- `backend/queries/teams.py`
- `backend/queries/matches.py`
- `backend/api/routes_public.py`
- `frontend/app/leagues/`
- `frontend/app/matches/`
- `scripts/build_local_preview.sh`
- `scripts/verify_next_assets.py`
- `tests/backend/test_five_critical_product_fixes.py`
- `frontend/e2e-product-fixes/`

明确不进入公众号配置、支付、模型训练、历史 backfill、真实采集、AWS 实际部署或
Studio 改版。完整实现说明与最终实跑结果见
`docs/audits/five-critical-product-fixes-v1.md`。

---

# Completed Module: Top-5 league season-table + player-stat backfill and /league v1 migration

## Status

COMPLETE(2026-08-04)。真实生产数据库写入 —— 与本文件其它条目不同,这一轮**确实**
写了 `data/allwin.db`(不是隔离沙箱/临时库),事前有独立备份。

## Goal

五大联赛(西甲/意甲/法甲/德甲,league_id 87/55/53/54)此前只有比赛级 Bronze,
缺 `fact_league_table`/`fact_season_player_stats` 聚合表;`/league/[id]/*`
四个前端页面仍调用未做付费门禁的 legacy `/api/league/{id}/overview` 端点。

## Real production write

167 次真实 FotMob 请求,0 失败,写入前完整三库备份
(`data/backups/allwin-pre-top5-backfill-20260803T161220Z.db`,
`PRAGMA integrity_check=ok`)。写入范围仅限 `fact_league_table` /
`fact_season_player_stats`(4 联赛各 6 个赛季的当季部分)/ `silver_team_season_stats`
等聚合表;`dim_match` 等 fact 层未动。`docs/current-state.md` §21 有完整命令记录。

## What shipped

- 新增 `/api/v1/leagues/{id}/team-stats`、`/api/v1/leagues/{id}/players` 两个端点
  (免费字段投影,付费深度字段物理不在响应体里);
- 修复一个真实生产 bug:赛程 season 过滤在 SQL LIMIT **之后**做,导致多赛季联赛
  的赛程页可能整页为空(`backend/queries/matches.py::list_matches` 新增
  `season` 参数,过滤下推到 SQL WHERE);
- 4 个 `/league/[id]/{standings,matches,team-stats,players}` 页面原地重写为调用
  `/api/v1/*`,新增客户端会员加载器 `MemberLeagueSection`(401/403 → 登录引导,
  404 → 联赛不存在,其它错误 → 可重试);
- `backend/cli/backfill_season_tables.py`(新 CLI)。

## Verification

768 pytest / 46 vitest / typecheck+lint+build 全绿(命令与真实输出见
`docs/current-state.md` §21)。Workflow 式多 agent 对抗复核当轮全部因平台
API 错误失败,改为人工执行等价检查(cache-header 矩阵、付费字段泄漏扫描、
CSS 完整性、测试弱化 diff 扫描),此事已在当轮向用户明确披露而非静默略过。

## Unverified / known boundary

四大联赛历史赛季(2020/2021–2024/2025)球员榜未回填(约 740 次额外请求,
留待需要时批量执行);西甲/德甲/意甲/法甲球队与球员绝大多数无中文名映射;
5 个既存 `e2e/anonymous.spec.ts` Playwright 失败经 `git stash` 基线隔离确认
与本轮改动无关(pre-existing)。

---

# Completed Module: Eliteserien(59) production ingestion + weekend odds acquisition test

## Status

IN PROGRESS(2026-08-04 开始)。生产库迁移 + 赛程回填 + 别名播种 + 本地轮询调度
已完成;真实赔率采集验收(`silver_odds_moves > 0`,本项目历史上从未达成过)
待 T-72h 窗口打开(round 17 首场 2026-08-07T17:00Z,窗口 2026-08-04T17:00Z 开启)
后才能产生证据,本条目届时更新。

## Goal

用本周末 7 场真实挪超比赛(round 17,round 16 的第 8 场 5104969 因提前踢完被
正确排除)在**生产**库上验证完整采集链路——赛程/精确kickoff/实体解析/赔率
快照/hash-diff 幂等——尤其是 `silver_odds_moves`,此前只在隔离沙箱产生过
单点观测,从未真正产出一条"变化"记录。

## Work items(本轮已完成部分)

- 生产三库迁移:`core/0002+0003`、`platform/0003~0006`(应用前先 commit 迁移
  文件,防止后续 `git clean` 造成 `schema_migrations` ledger 与文件不一致);
- `ingest_future_fixtures.py --league-id 59 --season 2026`:118 场真实抓取;
- `backfill_season_tables --league-id 59`:积分榜 80 行;
- 14 支挪超周末相关球队的 NowGoal 拼写别名手工播种,全部取自
  `runtime/artifacts/runs/eliteserien/*/nowgoal-schedule-*.raw.txt` 真实捕获
  产物(经 `nowgoal.parse_schedule` 解析,非猜测),`_alias_team_ids` 逐一验证
  FotMob/NowGoal 两侧拼写解析到同一 `canonical_team_id`,14/14 通过;
- macOS 本地 launchd 定时任务(`~/Library/LaunchAgents/com.allwin.poll.local.plist`
  + `deploy/scripts/poll_local.sh`,60 秒 tick,真实节流仍由 `poll_state`
  决定)——这台机器没有 systemd,`deploy/systemd/allwin-poll.timer` 无法运行,
  这是等价替代,不是新的生产部署机制。

## Permanent finding this round corrected

对抗复核发现并纠正了设计阶段的多个错误前提(周末实际 7 场非 8 场;首次采集
每场落 6 条快照非 3 条,因 `DEFAULT_TARGET_CIDS` 同时含 Bet365+Sbobet;
`--offline-fixture` 演练会写入生产 `poll_state`/`source_health`,必须用隔离
`ALLWIN_DATA_DIR`;`repair_kickoff_provenance.py` 只操作 platform.db 预测快照,
不能回填 `dim_match.kickoff_at_utc`)——完整清单见 `docs/data-plan.md` 开头
"对抗复核推翻的关键事实"表。

## Unverified(结构性,本轮设计已排除声称)

`silver_event_moves`/`gold_move_cooccurrence`(需要 FotMob 阵容/伤停快照,
生产库该两表为 0,`poll_fotmob_snapshots` 本轮未触发);T-72h/15min +
T-2h/5min 分级轮询节流精确验证(schema 无 per-match 尝试历史,多场比赛
同时处于不同档位时无法从现有表分离验证)。

评估证据与最终结论见 `docs/data-plan.md` §3、`docs/current-state.md`
本轮追加章节。
