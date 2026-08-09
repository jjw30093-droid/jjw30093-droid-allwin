# Douyin-safe Studio v1 audit

Date: 2026-07-29
Scope: local Studio and daily-content replay only
Deployment: not run

## Outcome

The Studio now has two explicit profiles:

- `douyin-safe-v1` is the default social profile when a real style profile is
  available.
- `internal-full-v1` preserves the existing prediction, market and complete
  analyst workflow.

The safe profile is not a CSS-hidden version of the full bundle. It is built
from an allowlist and serialized independently. It contains no prediction,
probability, 1X2, odds, market-baseline, bet, recommendation or return fields.
The public match API and anonymous probability entitlement contract are
unchanged.

## Real data source

The first real replay is Match `5104968`, Vålerenga v Hamarkameratene,
Eliteserien 2026. It reuses the saved team artifacts from the successful fresh
daily-content run:

- home team `8007`, artifact SHA-256
  `5786ea0cadd5774215ab6813a0bbc78e68bf019fdc362198288e002af9229beb`;
- away team `8448`, artifact SHA-256
  `25fa7fbe24448600b9823c723d07eca8edac0cb4dde2c1585745da9cff23cbcc`.

Before a profile is produced, the parser binds each artifact to the expected
team ID, league ID, season, standings row, played-match denominator and every
metric participant. Duplicate metrics, non-finite/negative values, invalid
ranks, and identity/season mismatches fail closed.

The paired source hash is
`aa8cccdd481d5f0a708ccbe24421d1307e99957661413ae46e85c55d82e9ea2f`.
The data cutoff is `2026-07-28T07:36:12Z`.

## Canonical metrics

The registry fixes labels, units, direction and conversion:

- direct percentages: possession;
- direct per-match provider values: accurate passes, shots on target,
  accurate crosses, final-third regains, tackles and clearances;
- season totals divided by matches played: xG, xGA, box touches, big chances
  and corners;
- season totals with league rank: set-piece goals and clean sheets.

The real sample has 14 played matches per team. Selected paired values are:

| Metric | Vålerenga | Hamarkameratene |
|---|---:|---:|
| Possession | 49.3% | 46.1% |
| Accurate passes | 357.9/match | 352.0/match |
| xG | 1.71/match | 1.44/match |
| Box touches | 32.3/match | 24.2/match |
| Corners | 7.1/match | 3.9/match |
| Set-piece goals | 3 | 6 |
| xGA | 1.89/match | 1.77/match |
| Tackles | 18.7/match | 12.1/match |
| Clearances | 29.1/match | 27.5/match |

`定位球进球` is the only set-piece label. No set-piece xG is collected,
derived or claimed.

## Storage and replay

Platform migration `0006_team_style_profiles.sql` adds an append-only,
versioned profile table. Its business key is `(match_id, data_cutoff_at)`.
Exact replay returns `skipped`; a changed payload under the same key raises a
conflict. UPDATE and DELETE are rejected by database triggers.

This migration was applied to gitignored runtime and isolated E2E databases
only. No real `data/*.db` database was migrated or written.

The following operator command regenerated the real profile and social copy
without provider access:

```bash
./scripts/daily_content.sh --league eliteserien --replay
```

The replay reported zero transport attempts. The second replay skipped the
existing profile and produced byte-equivalent content.

## Six scenes and exports

1. match cover;
2. possession and organization;
3. box threat;
4. width and set pieces;
5. off-ball and defensive pressure;
6. matchup summary, recent form, risks and WeChat CTA.

Every non-cover scene renders at most three metrics and one comparison
treatment. xGA is explicitly marked “越低越好”. A missing metric reduces the
scene rather than becoming zero. The output uses the existing deep-navy,
pitch-teal and restrained yellow system, real crests, direct value labels and
compact rank chips; no radar chart, black-gold template, match video or
copyright photograph is used.

The safe output set includes:

- six `1080×1920` PNG files;
- six `1080×1350` PNG files;
- safe JSON;
- 45–60-second voiceover;
- SRT;
- three titles;
- Xiaohongshu copy;
- WeChat summary.

Filenames include `douyin-safe-v1`, match ID, cutoff and source-hash prefix.
The right interaction strip and lower copy zone are reserved as empty visual
space, not labelled in the exported image.

## Safety gate

The safe profile, six card DOM trees, titles, voiceover, SRT, Xiaohongshu
copy, WeChat summary and exported JSON are scanned for:

`胜平负 / 主胜 / 客胜 / 平局 / 赔率 / 盘口 / 水位 / 投注 / 推荐 / 稳胆 /
命中率 / 收益 / 红单 / 1X2 / MARKET_BASELINE`.

Key names containing prediction, probability, odds, market or 1x2 are also
rejected recursively. Football-statistic percentages such as possession are
allowed.

## Verification

Permanent tests cover artifact identity/season binding, conversions, ranking,
missing and invalid values, duplicates, idempotent replay, append-only
conflicts, snake-case crest projection, six safe scenes, lower-is-better
presentation, safe serialization, filename provenance and DOM copy scanning.

Final validation:

- backend broad regression excluding the four destructive E2E-seed tests:
  **715 collected / 715 passed**;
- frontend Vitest: **7 files / 43 passed**;
- complete Playwright: **15/15 passed**, including safe-default Studio and
  real PNG/JSON/SRT download;
- typecheck, lint and OpenAPI drift: passed;
- fresh same-origin production build: **13/13 routes**, bundle loopback gate
  passed;
- custom production-preview checks at 390×844 and 1280×800: six scenes,
  no page overflow, no broken image and zero console warning/error;
- Studio light/dark checks at 390×844: no overflow, no broken image and zero
  console warning/error;
- 12 final PNGs: exact 1080×1920 or 1080×1350 dimensions, all visually
  inspected. The first visual pass exposed and fixed the `crest_url` mapping;
  the second exposed and fixed compact-layout footer overlap.

Final files are in:
`runtime/studio/douyin-safe-v1/`.

## Boundaries

- no AWS deployment;
- no network request for replay or Studio export;
- no production Worker/systemd/API authorization change;
- no real database migration;
- no commit or push;
- full internal analysis mode remains available and intentionally contains
  analyst-only prediction/market material.
