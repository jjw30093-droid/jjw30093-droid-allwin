# Schedule-state temporary production-copy migration/import trial

> Date: 2026-07-26  
> Scope: prepared exact `allwin.db` copies under `/tmp`; formal migrations
> `0002`/`0003`; legacy preservation; stable-identity cross-run backfill;
> offline CWC formal-schema import; failure/recovery rehearsal  
> Real database migration / production ingestion / Worker / API / frontend:
> **NOT STARTED**

## Verdict boundary

The original trial used
`/tmp/allwin-schedule-state-migration-trial.Snn50X/`; the safety closure used
fresh private directories below
`/private/tmp/allwin-temp-copy-closure.jUI4Bf/`. It did not use SQLite backup
against the source, did not checkpoint or change the source WAL/SHM, did not
read credentials, and made no network request. All trial database files were
regular mode `0600` files inside mode `0700` run directories.

Results:

- Trial workspace ownership: **VALIDATED**
- Hardlink/symlink resistance: **VALIDATED**
- Existing-target overwrite resistance: **VALIDATED**
- Source WAL and mutation gates: **VALIDATED**
- Prepared exact-copy main + destination/recovery sidecar binding:
  **VALIDATED AFTER SIDECAR CLOSURE**
- Complete destination/recovery basename companion pathsets:
  **VALIDATED AFTER UNKNOWN-COMPANION CLOSURE**
- Real 0001 → 0002 → 0003: **VALIDATED**
- Legacy row/content preservation: **VALIDATED**
- Migration idempotency: **VALIDATED**
- Failure rollback: **VALIDATED**
- File recovery: **VALIDATED**
- Historical identity eligibility: **VALIDATED FOR STABLE IDENTITY ONLY**
- Historical identity cross-run replay: **VALIDATED**
- Provider/provider-match-ID canonicalization: **VALIDATED**
- Direct-SQL identity enforcement: **VALIDATED**
- Historical state backfill: **BLOCKED / 0 ELIGIBLE**
- CWC formal-schema offline import: **VALIDATED**
- Current/as-of and feature finalization: **VALIDATED**
- Query/index evidence: **VALIDATED FOR THIS SINGLE-MACHINE TRIAL**
- Historical cache-producing round integrity: **FAIL**
- Historical destination-sidecar closure integrity: **PASS**
- Current unknown-companion closure integrity: **FAIL (NEW TASK-LOCAL PYCACHE)**
- Public restore failure atomicity: **UNVERIFIED (NO PUBLIC RESTORE API)**

These results do not authorize a real migration or production polling.

## Independent rejection and closure

The first report was independently rejected with
**P0=0 / P1=3 / P2=2 / `FIX_REQUIRED`**:

1. `migrate_exact_copy(path)` would migrate an arbitrary existing path that
   merely passed a broad temporary-path check. It did not prove exclusive
   creation, single-link ownership, source WAL state, or source/destination
   stability across preparation and migration.
2. Identity replay compared a new run's `created_at` with the stored first
   value. The old proof reused T0 and therefore did not establish real
   cross-run idempotency.
3. Provider and provider-match-ID inputs could produce duplicate logical
   identities or stringify unsupported objects.
4. The corresponding hardlink/symlink/WAL/mutation/replacement and
   T±1/normalization permanent counterexamples were missing.
5. The documents incorrectly promoted same-T0 immediate replay to long-term
   idempotency.

Permanent counterexamples were added before implementation. On the old code,
the combined selection produced **135 collected / 55 selected / 2 passed /
53 failed / 80 deselected**, with 0 skipped, xfailed, or warnings. That RED is
retained as history.

The replacement public workflow is
`prepare_trial_copy(source) -> PreparedTrialCopy ->
migrate_prepared_trial_copy(handle)`. Migration no longer accepts a raw
destination path. Preparation creates a new current-user mode `0700` run
directory, then exclusively creates mode `0600`, single-link destination and
recovery files. The handle binds creator PID/UID; run-directory device/inode;
source main/WAL/SHM fingerprints; destination/recovery device/inode/SHA-256;
and, after the later sidecar closure below, the complete recognized
destination/recovery SQLite sidecar set. The migration gate rechecks all of
them immediately before opening the copy writable.

The permanent matrix rejects source or parent symlinks, destination symlink
and hardlink forms, existing ordinary and SQLite targets, unsafe custom
parents, non-regular files, raw-path migration, non-empty source WAL, source
change during/after copy, run-directory or destination inode replacement,
link-count drift, mode drift, and owner mismatch. It performs no source
checkpoint and never removes or overwrites a caller-owned target.

### Later destination-sidecar rejection and closure

A subsequent final independent review found
**P0=0 / P1=1 / P2=2 / `FIX_REQUIRED`**. The earlier exact-copy conclusion
bound the destination main file but not its `-wal`, `-shm`, or `-journal`
state. An adversarial destination connection selected WAL mode, disabled
automatic checkpointing, committed a `dim_match` mutation, and stayed open.
The destination main-file SHA/inode/mode/link count remained byte-for-byte
stable, while a separate logical SQLite read observed the mutated value from
the non-empty WAL. The old public migration API accepted the prepared handle
and began migration. The two P2 findings were the missing permanent
destination-WAL mutation test and the over-broad documentation claim. This
finding does not affect the identity, normalization, 0003, legacy, or CWC
results, but it supersedes the old main-file-only exact-copy safety claim.

Tests preceded implementation. On the old code the sidecar selection was a
real **28 collected / 9 selected / 9 failed / 19 deselected** RED, with 0
skipped, xfailed, or warnings. It independently covered:

- committed destination WAL content with an unchanged main-file fingerprint;
- a zero-byte destination WAL introduced after preparation;
- destination SHM replacement and rollback-journal appearance;
- destination sidecar symlink and hardlink forms;
- a recovery-image WAL;
- a sidecar introduced after the last read-only integrity operation;
- a non-empty destination WAL left after migration work.

`PreparedTrialCopy` now stores immutable destination and recovery
`SQLiteSidecarSetFingerprint` values for `-wal`, `-shm`, and `-journal`.
Every present sidecar must be a current-user mode `0600`, single-link regular
file. A WAL must be zero bytes and a rollback journal must be absent.
Preparation records these sets only after successful read-only integrity
checks. Validation requires the original set and exact fingerprint before the
next integrity check, then rechecks it afterwards and immediately before
migration. SQLite read locking can advance only the SHM mtime, so that single
post-integrity comparison permits SHM mtime drift while continuing to bind
its path/device/inode/owner/mode/link count/size/SHA; the following final
comparison is exact. Migration success is not returned with a non-empty WAL
or rollback journal, and the recovery image plus sidecar set must remain
unchanged. A control test proves that an already-bound zero-byte WAL and
private SHM are accepted, so legitimate read-only WAL metadata is not
confused with a later mutation.

### Unknown companion pathset rejection and closure

A final narrow review found a further historical
**P0=0 / P1=1 / P2=2 / `FIX_REQUIRED`** boundary. The sidecar closure above
fingerprinted all three known SQLite names, but its fixed suffix enumeration
did not scan the full database-basename pathset. The old public gate ignored
names such as `trial.db-wal2`, `trial.db-mj ABCDEF12`,
`trial.db-journal.extra`, arbitrary extension/suffix names, and prefixed
symlinks, hardlinks, directories, or FIFOs. The same gap applied independently
to `recovery.db`. Permanent unknown-sidecar tests and an accurate
migration-lifecycle/public-restore boundary were missing. Identity,
normalization, migration SQL, legacy, and CWC behavior were not implicated.

Tests preceded the implementation change. The old code produced a real
**55 collected / 26 selected / 24 failed / 2 passed / 29 deselected** RED,
with 0 skipped, xfailed, or warnings. Destination and recovery each covered
`-wal2`, super-journal-shaped and journal-extension names, arbitrary suffixes,
ordinary files, symlinks, hardlinks, directories, and FIFOs. Additional cases
introduced an unknown entry after the read-only integrity check and after
runner work. The two passing controls were existing recovery journal/SHM
drift rejection; all 24 unknown/unbound cases were accepted by the old code.

The prepared handle now binds, separately for `trial.db` and `recovery.db`,
the full allowed companion-name set plus the existing full fingerprints for
known sidecars. An `os.scandir` basename-prefix scan includes every other
matching directory entry. Only the exact `-wal`, `-shm`, and `-journal` names
may proceed to known-sidecar validation. Every other matching name is an
unbound companion and fails closed before its contents are read or any
symlink is followed. Size 0, current ownership, mode `0600`, or regular-file
type never auto-admits an unknown name. The tool does not delete, truncate,
rename, or silently allow unknown entries.

The final pre-open sequence validates workspace/source, destination main and
complete pathset, then recovery main and complete pathset, performs the
read-only integrity check, and immediately repeats the destination checks
without a caller callback or wait. The migration owns the expected exact-name
SQLite lifecycle. After the first schema application closes all connections,
the exact known pathset is captured before the internal idempotency
application. After that closes, the final pathset is rebound and recovery is
rechecked before success. An unknown migration-time addition, non-empty WAL,
or any rollback journal rejects the result.

The enhanced committed-WAL test keeps the writer connection open with
automatic checkpointing disabled. It proves migration runner calls 0; exact
WAL device/inode/owner/mode/nlink/size/SHA-256/mtime_ns stability; unchanged
destination main identity; ledger version 1 only; zero schedule objects; and
the still-readable logical mutation in the WAL. No checkpoint, truncation,
or merge occurs before rejection.

Observed normal lifecycle evidence is not promoted to sidecar-inode
immutability. A reviewed `/tmp` run had WAL size 0 and SHM size 32768 before
migration; WAL peaked at approximately 181312 bytes while migration
connections were active; after close WAL returned to 0 and no journal
remained. WAL/SHM inodes may be recreated by SQLite. The main file contained
ledger 1/2/3 and schedule schema, `integrity_check=ok`, foreign-key findings
were 0, and the internal second application returned 0. The contract is
tool-owned lifecycle, logical commit in main, quiescent return, and a rebound
final pathset—not stable sidecar inode identity across writable work.

Recovery evidence is narrower than public restore atomicity. The recovery
main/companion fingerprints, historical manual `/tmp` file replacement
rehearsal, and clean-image remigration were checked. There is no public
restore API, so **`PUBLIC_RESTORE_FAILURE_ATOMICITY: UNVERIFIED`** remains an
explicit later real-migration/runbook blocker. This audit does not claim that
a failed public restore cannot partly replace a target or that a production
restore procedure has been validated.

## Source-copy gate and fixed manifest

The real source was read with `mode=ro&immutable=1`. Before and after the
ordinary file copy:

- source main SHA-256:
  `92a6a39c40dfb21f9dacfe6a8e8953f6b0a971ebb5b40a6ae9f253ad00ab364e`;
- source main size: `406073344`;
- source main inode: `99002671`;
- source main mtime: `1784470866000000000 ns`;
- existing WAL size/SHA-256:
  `0` /
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`;
- existing SHM size/SHA-256:
  `32768` /
  `fd4c9fda9cd3f9ae7c962b0ddf37232294d55580e1aa165aa06129b8549389eb`.

No source attribute changed during the copy. The mode `0600` pre-migration
copy had the exact same main-file SHA and size, `integrity_check=ok`, only
ledger version 1, and `dim_match=11115`.

The actual reviewed inputs were:

| Input | SHA-256 |
| --- | --- |
| Git HEAD | `cfe027283ab6318a1298d89d544eb9fa351fa713` |
| `backend/db/migrate.py` | `96064e88480f2e388543106fec50e7b2f6051b6a70264493055d54ecf1e9d2e5` |
| `0001_dim_match_kickoff.sql` | `ce612b9acdf3d4aff2888e149bab607d447f79226c1c6dc158e5c4571a117b70` |
| `0002_kickoff_provenance.sql` | `6374d78c240272e15e101b3480546663023621c0ef4b92ec9452a6aadfbc1a2b` |
| `0003_schedule_state_v1.sql` | `7e69e2a15b469ed9286345c0e21ebe49efdeb09561b2e2075bc0222dce050b57` |

The branch was `main`, exact tag was absent, stash was empty, and the dirty
worktree was retained rather than cleaned or trusted as a release tree.

## Exact-copy migration and legacy evidence

The current formal runner applied exactly two migrations. The final ledger was
strictly versions 1, 2, and 3 with the filenames and checksums above.
The recovery-image reapplication of the same formal migration took
`0.709802833 s`. Database size changed from `406073344` to `406351872`
bytes (`+278528`). The migrated copy had:

- `integrity_check=ok`;
- zero `foreign_key_check` findings;
- all 42 reviewed schedule tables/views/indexes/triggers;
- `dim_match=11115`;
- `kickoff_precision=date_only` for all 11,115 rows;
- `kickoff_source IS NOT NULL=0`.

All 18 pre-existing legacy tables were captured before migration with schema,
columns, indexes, row count, PK/rowid endpoints, and a typed deterministic
digest. The same original columns were hashed after migration:

| Legacy table | Rows | Original-column SHA-256 |
| --- | ---: | --- |
| `dim_match` | 11115 | `db6d6065b2b2e28a80159eea9c3cca644d82e5fed0404cc6a7d13877f6b4341b` |
| `dim_player` | 6271 | `2289d0f3cedfd2475aac9025dadbbaaf207d7862560f601730a33fb911ef90de` |
| `dim_player_i18n` | 1378 | `865a3dcabbc000373658b975e0792544de2d1b1608eaf223d25399df796fff08` |
| `dim_team_i18n` | 30 | `869c69c13755197dec2bb2e2137cd6656422f5fdd2fbcde7c10628615181f361` |
| `fact_league_table` | 600 | `76372810440cd7e298ded2813eabb6efd5b3b4e89e6defbb1c4dc7c206bb80d1` |
| `fact_match_events` | 212727 | `d85e48e289892f566c972efc03b25925c36c8b005be1158481f41f4f9f7f9b3f` |
| `fact_match_lineup` | 451161 | `91abbaa3d65de0d4680eac6766fb39735d81f15817e5ab714f610757277ed1e4` |
| `fact_player_match_stats` | 328083 | `4683be01f5e5d49c53abca064d0d2f9e3633ca848bac69ccf072bcb2b195eae5` |
| `fact_season_player_stats` | 59139 | `fae3404315211e505ee66b24f3fbafe2186ad84943200522894f23db1516b804` |
| `fact_shotmap` | 269071 | `e14bdb6145e84d9c844bcadb9881835cbf8c7a1d8ad6b0e095a541e99c8427d8` |
| `fact_team_match_stats` | 45468 | `a19237fae2d3a792a6385a721f67198da1187e60b2a637b3811ecc9fd9820795` |
| `gold_wdl_predictions` | 760 | `ec0043b911d2679d6aa6a9edab50e07899558cacc905d14f8509511b37348530` |
| `int_match_features` | 2280 | `3f64f905bfcc6ba8da74b05ffb719da6226340578bf4996d36521d780e18f0be` |
| `silver_goal_minute_buckets` | 42 | `7ba67de66b0124fa22f476a61988ce001843fc6218ca3c2c28d5b24d79d4ad1e` |
| `silver_league_season_summary` | 6 | `54ea2861740b101ca00aee4b518737e15c7d9138ee62077f9beab7822e6b4052` |
| `silver_over_under_thresholds` | 36 | `368bbcc27a2d78bacc8bab78523bd8c8d6889bf8b03602b564925180d9d3378f` |
| `silver_score_distribution` | 202 | `281769dacec5402a05b30f8f49cbcf0ebb31c3ebf90b11384bae4631488c6aad` |
| `silver_team_season_stats` | 120 | `7791aaea514515d48863d39d547e8fbec70ac0e91bbab6ab67855a0d979a4297` |

No original-column, row-count, PK/rowid endpoint, retained-index, or
non-`dim_match` schema difference was found. A second formal runner call
returned `applied=0` in `0.674008500 s`; DB SHA, size, and mtime_ns were all
unchanged.

## Failure and file-recovery rehearsals

An independent temporary manifest copied the current 0001/0002/0003 bytes and
added a fault at the end of its temporary 0003. The current formal runner
committed 0002, then the forced missing-table statement raised
`OperationalError`. Evidence after failure:

- ledger exactly 1 and 2;
- schedule-state object count 0;
- temporary fault-table count 0;
- all legacy original-column digests unchanged;
- all 11,115 new precision values correctly `date_only` from committed 0002;
- `integrity_check=ok` and no FK findings.

For file recovery, the migrated trial main/WAL/SHM were moved aside inside the
same temporary directory. The untouched mode `0600` recovery image was copied
back. Its SHA exactly matched the original pre-migration copy, its ledger was
only version 1, and `integrity_check=ok`. Re-running the formal manifest then
applied exactly 0002 and 0003 and restored all 42 objects.

## Historical identity eligibility and backfill

Repository source inspection establishes the table-level identity provenance:
the active FotMob ingestion paths obtain the fixture Match ID from FotMob and
persist it as `dim_match.Match_ID`; verify-DB merge inputs are produced by the
same ingestion path. This permits an identity-only trial mapping to
`provider=fotmob`, `provider_match_id=str(Match_ID)`, and
`canonical_match_id=Match_ID`.

The real-copy audit found:

- total / non-null / unique IDs: `11115 / 11115 / 11115`;
- duplicate / invalid IDs: `0 / 0`;
- missing competition / team references: `0 / 0`;
- exact `kickoff_at_utc` present: `0`;
- legacy status counts: `Finish=10735`, `NotStarted=380`;
- date-only legacy `Date` present: `11115`.

Therefore all 11,115 stable identities were eligible, but zero historical
state snapshots were eligible. Midnight was not synthesized; `Finish` was not
reinterpreted as verified `finished`; cancelled status and observation time
were not invented. All 11,115 rows require recollection before a historical
state snapshot may be recorded.

The formal service gained the missing narrow operation
`record_match_identity()`, which performs an exact immutable identity replay
without creating state or observation evidence. The first real-copy trial
inserted `11115`, skipped `0`, conflicted `0` in `1.154390458 s`. The exact
second run inserted `0`, skipped `11115`, conflicted `0` in `0.182194833 s`.
All canonical bindings matched, `dim_match` stayed at 11,115, and state,
observation, lineage, and feature tables stayed at zero.

Those two historical calls used the same T0 and proved only immediate
same-timestamp replay. They were not a sufficient cross-run idempotency proof.
The closure defines `created_at` as first-write provenance: it is validated and
stored on first insert, but is not part of equality for an existing immutable
identity and is never updated.

A new prepared exact-copy run inserted all **11,115** identities at T0 in
`1.344 s`. After closing and reopening the database, a T0+1-day run skipped
all **11,115**, with 0 inserted and 0 conflicts in `0.178 s`; the table still
had 11,115 rows and every `created_at` remained T0. Replaying all identities
with provider `" FOTMOB "` and zero-padded numeric ID strings also skipped all
11,115. A changed canonical binding raised the explicit immutable conflict.
`dim_match` stayed at 11,115, and snapshot/observation tables stayed at zero.

Provider values are now NFKC-normalized, trimmed, lowercased, and limited to a
32-character ASCII slug beginning with `a-z` and containing only
`a-z0-9_-`. Provider match IDs accept only positive non-bool integers or
bounded supported ASCII strings; all-numeric strings are canonicalized to a
positive decimal without leading zeroes. Unsupported types, zero/negative
values, whitespace/control/path forms, and unsupported Unicode fail closed.
The `schedule_match_identity` CHECK constraints independently reject
non-canonical direct-SQL values while retaining an explicitly tested supported
alphanumeric ID.

## Offline CWC formal-schema import

The permanent canonical fixture byte SHA remained
`020b6eabecd9ca004611863d3b3f5820ee12ea2f1ff8f203f51e73a1bbf276b1`;
its recorded saved-source SHA remained
`6e0007cecea78d59b7d771d689a0e24d45dc4b2bbf7776a3a58516fbb71bae3d`.
The fixture does not contain a trustworthy observation timestamp. The trial
therefore used the visibly synthetic evidence source
`trial_synthetic_observation:canonical_fixture` and event times
`2026-07-26T00:00:00Z` / `00:05:00Z`. `source_updated_at` remained `NULL`;
these times are not represented as provider update times or live validation.

First import:

- identity/snapshot/association inserted: `66 / 66 / 66`;
- finalized rest features inserted: `126`;
- import time: `0.032817875 s`;
- feature finalization time: `0.016339916 s`.

Exact replay at the same synthetic observation inserted nothing and skipped
all 66 identities/snapshots/associations and 126 features. The later
same-content observation inserted one new event and 66 associations, while
identities, snapshots, and features all remained unchanged. Final formal-table
counts were:

| Structure | Rows |
| --- | ---: |
| identities, including legacy | 11181 |
| CWC state snapshots | 66 |
| observation events | 2 |
| observation associations | 132 |
| lineage sets | 126 |
| ordered lineage inputs | 334 |
| finalized features | 126 |

The formal schema has no separate team-relation table. Its 66 snapshots each
carry immutable home/away endpoints, expressing the 132 source relationships
without creating a trial-only table.

Business evidence remained 66 current, 63 non-cancelled, 3 cancelled,
3 `AET`, and 126 finalized features. Manchester City (`8456`) retained four
features with gaps `NULL / 105 / 90 / 102`. No cancelled snapshot was a
feature target or lineage input. The consumer-facing feature input view
remained finalization-gated.

At T0, as-of selected the initial association; current selected the T1
association. Both pointed to the same immutable business snapshot. One measured
current lookup took `0.000201708 s`, and one as-of lookup took
`0.000110875 s`. `EXPLAIN QUERY PLAN` showed:

- identity lookup using the unique `(provider, provider_match_id)` index;
- as-of lookup using `idx_schedule_observation_current`;
- current view using indexed identity/association lookups and PK joins.

These are single-machine observations, not a production throughput promise.

## Permanent tooling and regression evidence

`analysis/schedule_state_migration_trial/` contains only explicit-source,
prepared-copy helpers and synthetic permanent tests. It never discovers a
production DB and has no transport. The source is opened only for byte copying
and read-only integrity evidence; migration can consume only the unchanged
process-local prepared handle.

The original identity-only state-service test produced a real RED because
`record_match_identity` did not exist. The later safety/identity closure RED
and its final permanent results are reported separately:

- copy-safety target: **19 collected / 12 selected / 12 passed /
  7 deselected**, 0 warnings;
- replay/normalization target: **139 collected / 48 selected / 48 passed /
  91 deselected**, 1 `StarletteDeprecationWarning`;
- trial tool full file: **19 collected / 19 passed**, 0 warnings;
- schedule-state full file: **120 collected / 120 passed**,
  1 `StarletteDeprecationWarning`;
- manifest/migration target: **20 collected / 20 selected / 20 passed**,
  1 `StarletteDeprecationWarning`;
- CWC prototype: **76 collected / 76 passed**;
- sealed CWC pilot: **41 collected / 41 passed**;
- team + competition pilots: **286 collected / 286 passed**;
- migrations + contract: **50 collected / 50 passed**.

Every command had 0 failed, skipped, and xfailed under `-W default`. The
Starlette/httpx warning is the same third-party deprecation source emitted
once per independent pytest process, not a product defect count; no warning
filter was used. `/tmp`-isolated compileall and `git diff --check` both
exited 0.

The later destination-sidecar closure was rerun independently under
`-W default`:

- sidecar target: **29 collected / 10 selected / 10 passed /
  19 deselected**;
- trial tool full file: **29 collected / 29 passed**;
- schedule-state full file: **120 collected / 120 passed**;
- migrations + contract: **50 collected / 50 passed**;
- CWC prototype: **76 collected / 76 passed**.

Every current-closure command had 0 failed, skipped, xfailed, and warnings.
This is current command output, not a rewrite of the earlier round's
Starlette warning evidence.

The unknown-companion closure then produced the following independent
`-W default` behavioral evidence:

- unknown companion: **55 collected / 26 selected / 26 passed /
  29 deselected**;
- committed WAL: **55 collected / 1 selected / 1 passed /
  54 deselected**;
- all known sidecar/companion cases: **55 collected / 34 selected /
  34 passed / 21 deselected**;
- trial tool full file: **55 collected / 55 passed**;
- schedule-state full file: **120 collected / 120 passed**;
- migrations + contract: **50 collected / 50 passed**;
- CWC prototype: **76 collected / 76 passed**.

Every command had 0 failed, skipped, xfailed, and warnings. `/tmp`-isolated
compileall and `git diff --check` are reported by the final execution record.
No network, credential, real database, real migration, sealed runner, Worker,
systemd, API, or frontend path was used.

The earlier closing integrity comparison found one task-local failure. The
early RED
run created
`analysis/cwc_production_integration_design/__pycache__/` at
`2026-07-26T04:36:03+08:00`, containing
`__init__.cpython-313.pyc` and
`cwc_production_integration_design.cpython-313.pyc`. This was after the
04:35:38 baseline: repository cache counts changed from
107 `__pycache__` / 808 `.pyc` to 108 / 810, and the worktree pathset changed
from 41,797 to 41,800 only for those three paths. They were not deleted,
touched, or falsely restored.

Branch, HEAD, tag, stash, Git status and untracked sets were unchanged. The
four real databases, the existing allwin/odds WAL/SHM set, canonical fixture,
six historical mode-0600 artifacts, and `.pytest_cache` all matched the
opening baseline. Thus that historical execution's integrity is permanently
**FAIL**. The destination-sidecar closure deliberately takes the resulting
108/810 cache state as a new baseline; it does not delete, touch, or claim to
restore those three paths. Its independent round-local integrity result is
**PASS** and never erases the historical failure. Opening and closing
branch/HEAD/tag/stash, Git status/untracked sets, four real databases and
existing WAL/SHM, canonical fixture, six historical artifacts, cache content
and metadata, `.pytest_cache`, and worktree pathsets matched exactly. Current
counts stayed at 108 `__pycache__`, 810 `.pyc`, nine `.pytest_cache` paths,
41,822 total paths, 40,524 paths excluding `.git`, and 51 untracked paths.
`READY_FOR_FINAL_TEMP_COPY_RE_REVIEW` is **YES** for this closure.

That PASS is historical and specific to the preceding sidecar round. During
the later unknown-companion implementation, a direct task-local `py_compile`
command incorrectly created
`analysis/schedule_state_migration_trial/__pycache__/` with
`schedule_state_migration_trial.cpython-313.pyc` and
`test_schedule_state_migration_trial.cpython-313.pyc`. Counts therefore moved
from that round's 108 `__pycache__` / 810 `.pyc` baseline to 109 / 812.
The new paths were not deleted, touched again, or disguised as restored.
Full path counts changed 41,834 → 41,837 and `.git`-excluded counts changed
40,524 → 40,527 only for those three paths. All pre-existing cache content
and metadata, Git status/untracked sets, four real databases and existing
WAL/SHM, canonical fixture, six historical artifacts, and `.pytest_cache`
matched the opening baseline.
Thus the unknown-companion behavior is validated, but its own round-local
integrity is permanently **FAIL** and
`READY_FOR_FINAL_UNKNOWN_SIDECAR_REVIEW` is **NO (INTEGRITY)**. Neither this
event nor the earlier cache-producing event is rewritten as a pass.

## Remaining boundary

- Real `data/allwin.db` migration/backfill: **NOT STARTED**
- Production source ingestion / persistent poll job: **NOT STARTED**
- Worker / systemd: **NOT STARTED**
- API / frontend: **NOT STARTED**
- Live CWC or any provider request: **NOT STARTED**
- Public restore failure atomicity: **UNVERIFIED**

No real-migration or production-integration action is authorized by this
evidence.
