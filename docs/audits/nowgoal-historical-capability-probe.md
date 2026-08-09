# NowGoal historical capability probe

Date: 2026-07-30

Module:
`analysis/nowgoal_historical_capability_probe/`

Runtime evidence:
`runtime/research/nowgoal-historical-capability/three-era-20260730/`

Web-archive runtime evidence:
`runtime/research/nowgoal-historical-capability/top5-2020-2021-web-archive-v5/`

## Purpose and transport boundary

This probe tested whether the currently verified NowGoal path can recover
historical initial/latest odds for the competitions covered by the FotMob
multi-season research run:

```text
saved validated FotMob schedule
→ historical Beijing date
→ NowGoal type=6 schedule
→ strict kickoff/team mapping
→ Titan ID
→ NowGoal type=14 initial/latest
```

It used `httpx.Client(trust_env=False)` and did not read or use
`THORDATA_PROXY`, `HTTP_PROXY`, `HTTPS_PROXY`, or `.env`. The result therefore
consumed no residential-proxy traffic. No SQLite database was opened for
mutation.

The request ledger and raw responses are private runtime artifacts. Errors
contain fixed messages only; request URLs, payloads, paths, credentials, and
system exception text are not exposed.

## Deterministic sample

The target manifest was rebuilt from the immutable, validated FotMob coverage
artifacts. For each of 11 historical competitions it selected the middle
finished, non-cancelled match from the early, middle, and late completed
provider seasons:

- 33 target matches;
- 11 competitions;
- 32 distinct Beijing calendar dates;
- requested date range `2015-07-04` through `2026-04-12`;
- exact FotMob Match ID, home/away, kickoff, source artifact, and source
  SHA-256 retained for every target.

This is a three-era capability sample. It is not proof about every historical
date or every possible undocumented NowGoal endpoint.

## Live result

The live run used 34 direct transport attempts:

- 32 historical type=6 schedule requests;
- one current schedule control for Beijing date `2026-08-01`;
- one current type=14 odds control for already verified Titan `2912857`;
- 34 `STARTED`, 34 `SUCCEEDED`, zero failed attempts, zero retry, zero WAF
  response;
- residential proxy used: `false`.

All 32 historical date responses returned valid JSON with `ErrCode=0`, but
their Data blocks contained `matchcount=0` and no `A[...]` match row. Therefore:

- historical schedule available: 0/33 targets;
- strict FotMob/NowGoal mapping: 0/33;
- historical Titan IDs discovered: 0/33;
- historical type=14 odds requests: 0;
- sampled historical 1X2/AH/OU availability: 0/33.

The controls separated historical unavailability from a general transport or
parser failure:

- the current schedule control returned 988 parsed matches through the same
  direct transport and parser;
- current Titan `2912857` returned two selected companies with 1X2, Asian
  handicap, and over/under; both initial and latest groups were present.

The result is therefore not caused by residential proxy configuration,
current NowGoal outage, WAF, type=6 parser failure, or type=14 parser failure.
It proves that the date-based `type=6` discovery path is unavailable for the
sample. It is retained as historical evidence and is not generalized to every
NowGoal archive surface.

## 2020–21 web-archive probe

A follow-up probe used NowGoal's season archive rather than historical
`type=6`:

```text
/league/2020-2021/<league_id>
→ immutable season JSON
→ historical Titan ID
→ timestamped AH/OU history
→ independent timestamped 1X2 company history
```

The five verified archive league IDs were Premier League `36`, Serie A `34`,
La Liga `31`, Bundesliga `8`, and Ligue 1 `11`. The probe selected the
deterministic middle match from each complete season catalog. The catalogs
contained 380, 380, 380, 306, and 380 matches respectively.

The live run made 40 direct attempts with `trust_env=False`; all succeeded,
with no residential-proxy traffic. For all five sampled matches:

- Bet365 returned timestamped pre-match 1X2, AH, and OU history;
- Macauslot returned timestamped pre-match 1X2, AH, and OU history;
- Pinnacle returned timestamped pre-match 1X2 history;
- provider rows at or after kickoff were classified separately and were not
  counted as pre-match evidence.

The independent 1X2 catalog verified the company identities and IDs:
Bet 365 `281`, Macauslot `80`, and Pinnacle `177`. The mix-history company IDs
used for AH/OU were Bet365 `8` and Macauslot `1`.

This is positive capability evidence for one deterministic 2020–21 sample per
league. It does **not** prove that every match, every market, or every season
from 2020–21 onward has complete history. Full backfill must retain per-match
availability and provenance rather than infer coverage from these five
samples.

## Odds semantics

Even the working type=14 control provides only provider fields `f` (initial)
and `l` (latest). It provides no verified source timestamp or complete change
timeline. This probe does not call `l` a closing price and does not claim it
was available at a particular historical decision time.

The old type=14 `f/l` control still has no verified source timestamps and must
not be called a closing price. The newly verified archive history is different:
its rows contain provider timestamps, enabling an explicit pre-match cutoff.

## Replay and tests

Replay used the same run ID, made zero network requests, retained 34 attempts,
and reproduced the same counts. The combined SHA-256 over the request ledger
and raw artifact hashes was
`e44436212cfeedf636a90297077df79ed728b5bdead21b520e4e82aacf475eff`
before and after replay.

Permanent tests cover deterministic three-era selection, raw-name recovery,
strict direct/inverted mapping, ambiguity rejection, `trust_env=False`,
budget-before-transport, retry/resume, checksum validation, live/replay,
initial/latest semantics, WAF fail-closed behavior, zero-network dry-run, and
safe exception chains. Archive tests additionally cover both season-catalog
shapes, deterministic match selection, company identity, timestamp
classification, live/replay, and the requirement that all three requested
companies be present before returning the positive archive verdict.

## Verdict

`NOWGOAL_CURRENT_DIRECT_PATH_VALIDATED`

`NOWGOAL_HISTORICAL_DATE_DISCOVERY_SAMPLED_UNAVAILABLE`

`NOWGOAL_TOP5_2020_2021_ARCHIVE_SAMPLE_AVAILABLE`

The archive probe removes the earlier “no verified archive endpoint” blocker
for the sampled 2020–21 matches. A full five-league backfill and its
completeness review have not been run.
