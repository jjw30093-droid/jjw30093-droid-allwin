# Fresh daily content operations

`scripts/daily_content.sh` is the single operator entry point. It uses the
same schedule, odds, analysis-bundle, and Studio code as the local product.

## Commands

```bash
# Provider acquisition, durable raw artifacts, DB apply, bundle and Studio files
./scripts/daily_content.sh --league eliteserien --fresh

# No network: verify and replay the latest successful artifact set
./scripts/daily_content.sh --league eliteserien --replay

# No network and no provider mutation: inspect the internal odds due policy
./scripts/daily_content.sh --league eliteserien --due --dry-run
```

`--match-id 5104968` selects a known match. `--free-outcome home|draw|away`
chooses the one probability physically present in the anonymous response; the
stable default is `home`.

## Studio social outputs

When the saved home/away team artifacts pass the team/league/season/sample
binding checks, daily-content also builds a versioned team-style profile and
the default `douyin-safe-v1` social package. No extra request is made: both
`--fresh` and `--replay` reuse the already persisted team responses.

The safe package is written below `ALLWIN_STUDIO_OUTPUT_DIR` with filenames
containing the profile, match ID, cutoff date and source-hash prefix. It
contains safe JSON, SRT, a 45–60-second voiceover, three titles, Xiaohongshu
copy and a WeChat summary. Open the Studio editor to export the six scenes in
both `1080×1920` and `1080×1350`.

The safe package is physically separate from `internal-full-v1` and does not
contain prediction, probability, 1X2, odds, market or recommendation fields.
It uses possession, organization, box threat, width/set-piece, off-ball and
defensive-pressure metrics. Set-piece output means real season
`定位球进球`, never set-piece xG. Missing metrics are omitted, not changed to
zero.

The content candidate horizon is seven days. Odds collection is a separate
policy: one initial observation is permitted above 72 hours, 15-minute
intervals apply from T-72h to T-2h, five-minute intervals apply from T-2h to
kickoff, and pre-match collection closes at kickoff. In-play is not part of
this workflow.

## Season identity boundary

The shared season resolver keeps calendar, cross-year, and tournament seasons
distinct. Calendar-year Eliteserien continues to resolve from the explicit
calendar rule. A cross-year or tournament season cannot be inferred from the
current month; it needs an advertised provider label or a reviewed exact
mapping and must return the exact requested season.

The multi-league research probe does not enable `--fresh` for any additional
league. Its `SAMPLED_SAFE` historical findings are research inputs only. See
`docs/audits/multi-league-season-coverage-probe-v1.md`.

## Durable directories

Local development defaults to the gitignored `runtime/` directory. Production
should set:

```text
ALLWIN_DATA_DIR=/var/lib/allwin/data
ALLWIN_ARTIFACT_DIR=/var/lib/allwin/artifacts
ALLWIN_STUDIO_OUTPUT_DIR=/var/lib/allwin/studio
ALLWIN_MEDIA_DIR=/var/lib/allwin/media
ALLWIN_CONTENT_HORIZON_DAYS=7
ALLWIN_MAX_REQUEST_ATTEMPTS=20
ALLWIN_API_BASE=http://127.0.0.1:8000
```

The artifact pointer changes only after a complete successful run. A provider
failure keeps the last successful databases and artifact pointer and changes
the public state to `STALE` (or `UNAVAILABLE` if no success exists).

## AWS Tokyo layout (prepared, not installed)

Use an `allwin` system user and group in `ap-northeast-1`:

```bash
sudo install -d -o allwin -g allwin -m 0750 \
  /var/lib/allwin/data /var/lib/allwin/artifacts /var/lib/allwin/studio \
  /var/lib/allwin/media
sudo install -d -o root -g allwin -m 0750 /etc/allwin
sudo install -o root -g allwin -m 0640 \
  deploy/allwin-content.env.example /etc/allwin/content.env
```

Review, then copy (do not install from a working tree without a release
process):

```text
deploy/systemd/allwin-daily-content.service
deploy/systemd/allwin-daily-content.timer
deploy/systemd/allwin-odds.service
deploy/systemd/allwin-odds.timer
deploy/systemd/allwin-lineup.service
deploy/systemd/allwin-lineup.timer
deploy/systemd/allwin-content-health.service
deploy/systemd/allwin-content-health.timer
```

The daily timer refreshes schedule/standings and rebuilds content at 06:15 and
18:15 Asia/Shanghai. `allwin-odds`/`allwin-lineup` (PIPELINE_REDESIGN_V2 P3;
the earlier combined `allwin-poll` timer no longer exists) each fire a
five-minute due-check for NowGoal odds and FotMob lineup/sideline
respectively, invoking the same persisted `poll_state` due checks; frequent
timer activation does not imply an external request. The health timer checks
the API and database every five minutes without contacting a provider.

## Health, retention, backup, and logs

- `GET /healthz` and `GET /readyz` are the process and database probes.
- `content_status.json` records the last attempt, last success, next planned
  sync, observation count, and `LIVE / STALE / UNAVAILABLE` projection.
- Keep successful provider run directories for at least 30 days; retain the
  latest successful manifest indefinitely, then expire older immutable runs by
  an explicit reviewed retention job.
- Back up all three SQLite files with:
  `ALLWIN_DATA_DIR=/var/lib/allwin/data deploy/scripts/backup_sqlite.sh`.
- systemd defaults to journald. If output is redirected to
  `/var/log/allwin/content.log`, use `deploy/logrotate/allwin-content`.

## Cloudflare origin

Cloudflare terminates public TLS and connects to the Nginx origin with
Full (strict). Keep FastAPI and Next.js on loopback, use the existing
`deploy/nginx/allwin.conf.example`, route `/api/*`, `/healthz`, and `/readyz` to
FastAPI, and route the remaining paths to Next.js. Keep authenticated API and
RSC responses out of shared cache. Do not put a localhost browser API base into
the production bundle; `ALLWIN_API_BASE` is an operator/service setting, while
the existing frontend origin rules remain authoritative.

These files are deployment preparation only. No AWS instance, systemd unit, or
Cloudflare setting is changed by the repository task.
