# Active-league content MVP and fresh daily pipeline

Date: 2026-07-28

## Product outcome

The Eliteserien 2026 vertical slice now has a durable, one-command fresh
workflow. The content candidate window is seven days and is intentionally
separate from the odds high-frequency window. A real fixture does not need to
be inside 72 hours to appear on the website or in Studio.

The retained real sample is:

- FotMob competition `59`, `Eliteserien`, season `2026`;
- FotMob match `5104968`, Vålerenga v Hamarkameratene;
- NowGoal/Titan match `2912857`;
- kickoff `2026-07-31T17:00:00Z`;
- exact home/away direction, zero-second kickoff difference, confidence `1.0`.

No demo fixture or fabricated future match was used. All live writes went to
the gitignored `runtime/` tree; the four existing `data/*.db` files were not
migrated or written.

## Fresh, replay, and due modes

The single operator entry point is:

```sh
./scripts/daily_content.sh --league eliteserien --fresh
```

It composes the existing FotMob transport, strict response handling,
pagination inspector, NowGoal provider, odds hash-diff ingestion,
schedule/state migrations, active-league analysis bundle, and Studio source
export. It does not introduce a parallel acquisition framework.

Two non-network modes are permanent:

```sh
./scripts/daily_content.sh --league eliteserien --replay
./scripts/daily_content.sh --league eliteserien --due --dry-run
```

`--match-id 5104968` pins the sample. `--free-outcome home|draw|away` selects
the only probability physically present in the anonymous DTO; the stable
default is `home`.

The real acceptance run used 22 provider attempts in total across an initial
safe local-DB initialization failure and three successful fresh runs, below
the hard ceiling of 50. The final successful run, after the assembler was
parameterized, used five requests because the existing odds observation was
not due. Replay and due dry-run each used zero network requests.

## Real data and odds semantics

The isolated runtime holds 240 fixture rows, 80 table rows, eight recent
matches per team, team season data including available xG/xGA, one match
detail artifact, and six real NowGoal records covering 1X2, Asian handicap,
and over/under for Bet365 and Sbobet.

The current Bet365 1X2 snapshot is `1.62 / 4.10 / 4.75`; the de-vigged
probabilities are:

- home `57.5979%`;
- draw `22.7582%`;
- away `19.6439%`.

They are labelled `MARKET_BASELINE`, never `MODEL`. There is one system
observation point. Unchanged polling does not append another business
snapshot, and the product says “当前赔率” rather than drawing a false
movement curve. Poll/run facts remain recordable. Pre-match polling closes at
kickoff; in-play is excluded; FINAL means the last real pre-match snapshot.

The schedule and content horizon is seven days. Odds policy remains one
initial observation above T-72h, every 15 minutes from T-72h to T-2h, and
every five minutes from T-2h to kickoff. At the actual acceptance time the
sample was still outside the 72-hour high-frequency window, so the next due
time was naturally `2026-07-28T17:00:00Z`; no clock was forged.

## Website and Studio

The production build is available locally through
`http://127.0.0.1:3400`. The in-app browser verified:

- the home page lists the real sample with Chinese team names, localized
  kickoff, LIVE state, update time, source, and one odds observation;
- the anonymous detail shows only home `58%`; the response keys are exactly
  `meta`, `tier`, `top_outcome`, and `top_probability`;
- the Premium control shows all three probabilities and the real 1X2/AH/OU
  tables;
- the detail shows last success, next planned sync, source, and current-odds
  count;
- Studio opens the same match and exports real PNG, JSON, and SRT files;
- the browser console had zero warnings or errors.

The exported PNG files were independently checked as real PNGs with dimensions
`1080×1920` and `1080×1350` and sizes 454,765 and 383,045 bytes. JSON contains
match `5104968`, the real teams and `MARKET_BASELINE`; SRT contains valid
subtitle timing. The durable Studio directory also contains a Chinese
voiceover, three title candidates, and a WeChat article summary. The public
account block remains a neutral configurable placeholder and does not invent a
QR code.

## Durable operation and deployment preparation

Local defaults are under gitignored `runtime/{data,artifacts,studio}`.
Production paths are supplied through `ALLWIN_DATA_DIR`,
`ALLWIN_ARTIFACT_DIR`, and `ALLWIN_STUDIO_OUTPUT_DIR`; no random `/tmp`
directory is required for normal operation. Horizon, request ceiling and API
base are also configurable.

Eliteserien is a formal verified league config. Allsvenskan and MLS are present
as configuration-only entries and cannot run fresh promotion until their
identity/mapping gates are explicitly enabled; the pipeline is not copied per
league. The artifact assembler now receives the selected league/match/team
context instead of retaining sample IDs internally. Reviewed aliases are used
when present; otherwise exact live candidate team names drive mapping, so the
workflow can advance beyond match `5104968`.

Prepared, not installed or deployed:

- daily systemd oneshot and timer;
- EnvironmentFile example;
- logrotate example;
- health, backup, artifact-retention and AWS Tokyo directory guidance;
- Cloudflare/Nginx origin guidance.

No AWS instance, systemd unit, Cloudflare setting, real migration, payment
flow, or production OAuth setting was changed.

## Verification

- fresh/replay/due implementation plus API, odds, schedule-state, migrations
  and contract scope: `335 collected / 335 passed`;
- standalone NowGoal/provider file: `25 collected / 25 passed`;
- frontend: `3 files / 28 tests passed`;
- frontend typecheck: passed;
- production build: passed, 12/12 static pages;
- browser bundle loopback check: passed;
- one third-party Starlette/httpx deprecation warning source; no skip or xfail.

One combined pytest ordering exposed an existing test-isolation assumption:
the proxy-missing test monkeypatches `dotenv.load_dotenv` after another module
has already imported its symbol. It passed 25/25 in its standalone required
environment and is recorded as test debt, not hidden as a product failure.

P2 deployment/content debt that does not block the validated sample:

- newly selected teams without a reviewed Chinese translation fall back to
  the real provider name; translations must be added as league configuration,
  never generated or guessed;
- Allsvenskan and MLS remain configured but fresh-disabled;
- this macOS host has no `systemd-analyze`, so unit-file runtime verification
  remains an EC2 preflight even though repository syntax and paths were
  reviewed.

## Status

- Fresh acquisition: **VALIDATED**
- Offline replay: **VALIDATED**
- Seven-day content selection: **VALIDATED**
- Odds due policy and one-point honesty: **VALIDATED**
- Real website and API entitlement slice: **VALIDATED**
- Daily Studio workflow: **VALIDATED**
- AWS deployment package: **PREPARED, NOT DEPLOYED**
- Real legacy databases: **UNCHANGED**

The product delivery labels are:

`FRESH_DAILY_CONTENT_PIPELINE_READY`

`ACTIVE_LEAGUE_MVP_READY`

`DAILY_STUDIO_WORKFLOW_READY`

`AWS_DEPLOYMENT_PACKAGE_READY`
