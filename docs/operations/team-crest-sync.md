# Team crest synchronization

Team crests use one provider-aware pipeline:

```text
FotMob Team ID in dim_match
→ explicit offline sync CLI
→ validated local PNG + manifest
→ same-origin versioned media API
→ shared TeamBadge component
```

The current canonical schedule rows use FotMob Team IDs, so the implemented
provider is explicitly `fotmob`. A future provider must add its own identity
mapping and validator; its IDs must not be treated as FotMob IDs implicitly.

## Local paths

`ALLWIN_MEDIA_DIR` controls the persistent cache. Development defaults to the
gitignored `runtime/media` tree:

```text
runtime/media/team-crests/manifest.json
runtime/media/team-crests/fotmob/{team_id}.png
```

The manifest records provider, provider team ID, relative path, SHA-256,
dimensions, byte size, fetch time, and the server-only source URL. Public API
responses never expose the source URL.

## Synchronize a league season

The command reads unique home/away IDs from existing `dim_match` rows. It does
not fetch schedules or modify SQLite:

```bash
ALLWIN_DATA_DIR=runtime/data \
ALLWIN_MEDIA_DIR=runtime/media \
.venv/bin/python -m backend.cli.sync_team_crests \
  --league-id 59 \
  --season 2026 \
  --provider fotmob
```

The default request ceiling is 30, timeout is 10 seconds, and one retry is
allowed. Valid existing manifest/SHA/PNG entries are skipped without a
request. Use `--force` only for an intentional refresh:

```bash
.venv/bin/python -m backend.cli.sync_team_crests \
  --league-id 59 --season 2026 --provider fotmob --force
```

Exit code is zero only when every selected team is available or already
valid. A single failure is reported as `UNAVAILABLE`, other teams continue,
and an older valid crest remains active. Missing crests never block schedule,
prediction, analysis, or daily-content processing; `TeamBadge` renders a
stable shield fallback.

## Validation and serving

Downloads are allowed only from `images.fotmob.com`, with redirects disabled
and environment proxy inheritance disabled. The sync accepts only HTTP 200
`image/png`, PNG magic/chunk integrity, 16–1024 pixel dimensions, and files no
larger than 1 MiB. HTML, JSON, SVG, empty, truncated, oversized, and invalid
dimension responses are rejected. Image and manifest writes use a same-
directory temporary file, file fsync, atomic rename, and directory fsync.

The resolver returns only:

```text
/api/v1/media/team-crests/fotmob/{team_id}.png?v={sha256-prefix}
```

The media route revalidates the manifest entry, exact relative path, regular
single-link file, SHA-256, dimensions, and requested version. The configured
media root, `team-crests`, and provider directory must also be real
non-symlink directories. File or directory symlinks, hardlinks, path
traversal, missing files, and mismatches return 404. The route never performs
a remote request. Successful responses are `image/png` with an ETag and
`public, max-age=31536000, immutable`.

## AWS first start and new leagues

Create one persistent cache beside the other runtime data:

```bash
sudo install -d -o allwin -g allwin -m 0750 /var/lib/allwin/media
```

Set this in the API and operator EnvironmentFiles:

```text
ALLWIN_MEDIA_DIR=/var/lib/allwin/media
```

After the schedule database is present, run the sync once as the `allwin`
service user. Repeat the same command when a new league/season is added; a
normal rerun is idempotent and requests only missing crests. Back up the
manifest and PNG tree with the runtime data, or regenerate it explicitly from
verified schedule identities.

Page requests never hotlink or fetch from FotMob. Use of club crests remains
subject to the source service terms and the clubs' trademark rules; technical
caching does not itself grant publication rights.
