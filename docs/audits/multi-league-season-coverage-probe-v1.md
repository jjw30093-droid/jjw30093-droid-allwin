# Multi-league season coverage probe v1

Date: 2026-07-30
Module: `analysis/multi_league_season_coverage/`
Runtime evidence:
`runtime/research/league-coverage/multi-league-season-coverage-v1/`

## Verdict

`MULTI_LEAGUE_SEASON_COVERAGE_PROBE_VALIDATED`

`CROSS_YEAR_SEASON_RESOLUTION_VALIDATED`

`READY_FOR_ISOLATED_HISTORICAL_BACKFILL`

These labels authorize only design and execution against a new isolated
database. No historical backfill, production database mutation, migration,
Worker, systemd unit, API promotion, frontend change, or deployment was run.

## Scope and controls

The probe used the existing `FotMobClient` live transport and proxy
configuration. It did not introduce another HTTP stack. Every possible
transport attempt was recorded as `STARTED` before dispatch in a private
JSONL ledger; one retry was allowed per acquisition call, and budget
exhaustion is checked before transport. The durable ceiling was 800 attempts.

All raw responses and reports are under the gitignored runtime directory.
Directories are mode `0700`; every file is mode `0600`. The ledger records
only fixed operation labels, safe request keys, attempt ordinals, timestamps,
sizes, and SHA-256 values. It contains no proxy credential, Authorization
value, cookie, response body, or authenticated URL.

No real SQLite database was opened for mutation. No NowGoal request was made.
No backfill, migration, commit, push, tag, stash, clean, or deployment was
performed.

## Durable run result

The completed live/recovery run consumed 508 of 800 transport attempts:

- 494 `SUCCEEDED`;
- 14 `FAILED`, retained in the append-only ledger;
- every failed required artifact was later obtained through an explicit
  resume;
- all 14 target competitions or controls ended `COMPLETED`;
- 127 provider seasons/structures passed identity, returned-season,
  non-empty fixture, unique Match ID, kickoff/team identity, status, and
  known-pagination gates;
- 351 finished, non-cancelled matches were selected deterministically at
  early/middle/late season positions;
- the final all-competition replay made zero new transport attempts.

The SHA-256 summary over the request ledger and all raw artifacts was
`55787a909f498cdec449eaf19ad795f175e6e7fa16e9c7d39e9012e233febf8f`
both before and after final replay.

The final report is a replay-derived view over immutable live artifacts.
`coverage-report.json` therefore records `mode=replay` while retaining the
actual 508-attempt ledger count.

## Verified competition identity and season coverage

| Competition | FotMob ID | Provider seasons in scope | Completed seasons sampled |
|---|---:|---:|---:|
| MLS | 130 | 12 | 2015–2025 (11) |
| J. League | 223 | 13 | 2015–2026 (12) |
| K League 1 | 9080 | 12 | 2015–2025 (11) |
| A-League | 113 | 12 | 2015/16–2025/26 (11) |
| Eredivisie | 57 | 12 | 2015/16–2025/26 (11) |
| Championship | 48 | 12 | 2015/16–2025/26 (11) |
| Liga Portugal | 61 | 12 | 2015/16–2025/26 (11) |
| Serie A (Brazil) | 268 | 12 | 2015–2025 (11) |
| Champions League | 42 | 11 | 2015/16–2025/26 (11) |
| Europa League | 73 | 11 | 2015/16–2025/26 (11) |
| Conference League | 10216 | 5 | 2021/22–2025/26 (5) |
| Premier League control | 47 | 1 | 2024/25 |
| Eliteserien current control | 59 | 1 | active structure only |
| Allsvenskan current control | 67 | 1 | active structure only |

A-League ID 113 was not guessed. The first exact search returned an immutable
empty result. A second FotMob search for `A-League` returned one Australian
league candidate (`id=113`) and separately returned A-League Women
(`id=9495`). The league schedule then independently returned
`details.id=113`, `details.name=A-League`, and `selectedSeason=2026/2027`
before ID 113 was accepted.

Brazil ID 268 was confirmed, but the source canonical name is `Serie A`, not
the earlier unverified registry guess `Brazilian Serie A`. The registry was
corrected only after the saved live response established that fact.

## Season resolution

`backend/season_resolver.py` separates:

- `CALENDAR_YEAR`;
- `CROSS_YEAR`;
- `TOURNAMENT_SEASON`;
- canonical season key;
- exact provider request parameter;
- returned provider season;
- verification status and evidence.

Calendar seasons are deterministic. Cross-year and tournament seasons are
never inferred from the current month: the exact provider label must be
advertised or explicitly reviewed, and the returned season must equal the
request.

The live J. League response exposed a real structure transition: it advertises
both calendar `2026` and cross label `2026/2027`; the latter response has 380
fixtures. The probe preserves 2015–2026 as calendar seasons and records the
advertised `2026/2027` label as `TOURNAMENT_SEASON` with anomaly
`SEASON_REGIME_TRANSITION`. It does not discard the response or force it
through a calendar parser.

Active partial seasons are recorded for structure but excluded from sampled
historical continuity. Conference League correctly begins at its actual
2021/22 first season.

## Structure evidence

`season-structure.csv` retains round and group values rather than flattening
them into a league-only model:

- A-League contains playoff rounds such as `1/4`, `1/2`, and `final`;
- J. League contains phase/final-style round values, including the 2026
  transition structure;
- K League 1 retains the full split-season round range;
- Champions League and Europa League show group letters before 2024/25 and
  league-phase/playoff round values from 2024/25;
- Conference League starts at 2021/22 and similarly records its group versus
  league-phase transition.

The source did not populate a useful fixture-level `stage` value in these
responses; stage semantics are therefore represented by the observed
round/group columns and are not fabricated.

## Coverage and safe-start semantics

Each sampled match records applicability, presence, non-null, literal zero,
positive, invalid, string, percent, rank, source segment, source path, and
sample Match ID. Categories remain separate: core xG, shot data, team style,
match context, player data, and physical data.

`first_contiguous_safe` is emitted only when at least two consecutive
completed seasons meet the threshold. It is labelled `SAMPLED_SAFE`, never
full-season complete. A single usable control season is `PROVISIONAL`.

The main sampled core xG/shot-xG safe starts are:

| Competition | Core xG / shot xG sampled-safe from |
|---|---|
| MLS | 2020 |
| J. League | 2022 |
| K League 1 | 2022 |
| A-League | 2020/21 |
| Eredivisie | 2020/21 |
| Championship | 2020/21 |
| Liga Portugal | 2020/21 |
| Serie A (Brazil) | 2023 |
| Champions League | 2020/21 |
| Europa League | 2020/21 |
| Conference League | 2021/22 |

Brazil has xG paths from 2020, but the three-match 100% threshold is not
continuous until 2023; the earlier presence is not promoted to safe.

Possession and accurate-passes sampled-safe starts vary independently. For
example, MLS reaches both in 2016, J. League in 2020, K League 1 in 2022,
A-League possession in 2017/18 and accurate passes in 2018/19, and Conference
League in 2021/22. The detailed per-field result, breaks, and source paths are
in `safe-start-seasons.csv` and `field-coverage.csv`.

Physical fields are `UNVERIFIED` when absent. The samples found no top-speed
or distance evidence in the domestic leagues. Champions League samples do
contain those paths in 2024/25 and 2025/26; that is only two-season sampled
evidence and does not rewrite the prior SQLite top-five historical audit,
whose all-zero physical columns remain a separate ingestion/storage finding.

## Data-shape findings

The final anomaly set contains no blocking competition failure:

- 11 `XGOT_NULL_ZERO_ENCODING_SHIFT` findings, confirming the known
  NULL-to-literal-zero representation change across every sampled historical
  competition with sufficient span;
- 10 active/partial-season exclusions;
- one J. League `SEASON_REGIME_TRANSITION`;
- two player-position mixed-shape review findings.

The provider also has a real abandoned Championship match with
`finished=true`, `cancelled=true` and an explicit `Abandoned` reason.
The parser accepts that documented provider combination but excludes it from
the sample denominator. The same boolean combination without an explicit
cancellation/abandonment reason remains invalid and fails closed.

## Replay, resume, and artifact contract

The CLI supports:

```text
--live
--replay
--dry-run
--competition <key>
--season <provider label>
--max-attempts <n>
```

Successful request keys bind immutable JSON bytes by SHA-256 and size. Resume
reuses them without transport. A missing or checksum-mismatched artifact
fails replay. Failed competitions do not stop later competitions. Dry-run
does not construct a live transport.

Required artifacts are all present:

- `manifest.json`
- `request-ledger.jsonl`
- `competition-season-summary.csv`
- `sampled-match-coverage.csv`
- `field-coverage.csv`
- `season-structure.csv`
- `safe-start-seasons.csv`
- `anomalies.csv`
- `coverage-report.json`
- `coverage-report.md`
- `raw/`

## Verification boundary

Permanent tests cover season conversion and mismatch, no month guessing,
identity, fixture and pagination gates, deterministic/cancelled sampling,
field semantics, two-season safe starts, A-League discovery, request budget,
retry, safe errors, checksum validation, private permissions, resume,
zero-network replay, single competition/season, failure isolation, manifest,
and dry-run.

This probe validates a research decision boundary. It does not prove every
match in every historical season contains every field, and it does not make
an isolated historical backfill safe without its own destination schema,
transaction, provenance, reconciliation, and rollback design.
