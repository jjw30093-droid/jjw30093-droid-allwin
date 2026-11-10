# Report missing-stat sample audit (2026-11-11)

User requested ten samples rather than exhaustive auditing. Source: read-only CC Opta database, adopted detailed-player raw_json and primary stat view. No database downloads or source writes.

Match: Manchester United vs Aston Villa, provider ID 4tp842gtjqyx0bbp23n3em9zo, kickoff 2026-11-08T16:30:00Z. Adopted raw SHA256: d2f485fd67612c216a15de14b59f36a7a1cf7c32accf1e10d8715e2b7fa10578.

Result: nine sampled count omissions are sparse zeros, one is an unused substitute. This is NOT evidence that every missing metric is zero. Old interceptionWon consumer keys also disagreed with actual Opta interception; Alysson has interception=2.

```json
[
  {
    "name": "H. Maguire",
    "metric": "totalAttAssist",
    "value": 0,
    "status": "zero_omitted",
    "minutes": 49.08333333333333
  },
  {
    "name": "H. Maguire",
    "metric": "totalCross",
    "value": 0,
    "status": "zero_omitted",
    "minutes": 49.08333333333333
  },
  {
    "name": "H. Maguire",
    "metric": "aerialWon",
    "value": 0,
    "status": "zero_omitted",
    "minutes": 49.08333333333333
  },
  {
    "name": "H. Maguire",
    "metric": "interception",
    "value": 0,
    "status": "zero_omitted",
    "minutes": 49.08333333333333
  },
  {
    "name": "H. Maguire",
    "metric": "saves",
    "value": 0,
    "status": "zero_omitted",
    "minutes": 49.08333333333333
  },
  {
    "name": "Bruno Fernandes",
    "metric": "aerialWon",
    "value": 0,
    "status": "zero_omitted",
    "minutes": 102.86666666666666
  },
  {
    "name": "Bruno Fernandes",
    "metric": "totalTackle",
    "value": 0,
    "status": "zero_omitted",
    "minutes": 102.86666666666666
  },
  {
    "name": "Bruno Fernandes",
    "metric": "interception",
    "value": 0,
    "status": "zero_omitted",
    "minutes": 102.86666666666666
  },
  {
    "name": "Alysson",
    "metric": "totalCross",
    "value": 0,
    "status": "zero_omitted",
    "minutes": 74.63333333333333
  },
  {
    "name": "M. de Ligt",
    "metric": "totalPass",
    "value": null,
    "status": "not_played",
    "minutes": 0
  }
]
```

## Rule repair

Registered player count zeros are restored only for identity-verified, actually participating players with nonempty valid detailed stat lists. The existing sparse-zero count contract is used; totalAttAssist and touchesInOppBox supplement the old seed. Empty/invalid lists, invalid/conflicting values, missing player identities and unused substitutes never receive generated zeros. xG/xA, model scores, percentages and arbitrary fields do not inherit this policy.

Valid raw values omitted by the normalized read are recovered from that same adopted player record, with raw_recovered provenance. Each match carries feed, definition version, participation and per-stat resolution. UI and PDF consume those saved values; exports never infer missing zeros from old snapshots. Interception consumers now use the actual source field.

Source participation and selected-window minutes are separate: a player can have whole-match counts while having zero minutes in the selected 11v11 window. Missing selected metrics then say no selected-window sample, not did-not-play. Unlinked scores mean only that this snapshot has no linked score, not that CC lacks source data.

## Real verification

Manchester United-Villa postmatch and Sunderland-Chelsea reconstructed prematch (five matches each) computed from live read-only CC data; generated into isolated preview storage. Prior reviewed production snapshots were not overwritten. New source metadata is part of new report versions.

Source focused tests:47 passed. Website stats/PDF/workflow tests executed; frontend stat/export tests6 passed. ESLint and changed component dependency TypeScript checks passed. Whole frontend tsc encounters existing unrelated test type mismatches and is not claimed passed. No new production build/deployment in this follow-up.

Preview: http://127.0.0.1:3087/studio/reports?report=7cc099e7-28cc-4122-bfc7-7007c1bf4c58#report-roles
Evidence projections: /tmp/cc-stat-{post,pre}-20261111.json. PDFs: /tmp/cc-stat-man-villa-20261111.pdf and /tmp/cc-stat-sun-che-pre-20261111.pdf. These small projections are not database copies.

Final validation: source47 passed; website24 passed (one existing Starlette deprecation warning); frontend6 passed; scoped TypeScript and ESLint passed. Reader PDFs26/28 pages, no blank pages, all pages rendered; changed count pages visually checked.11 protected local database/sidecar sizes and mtimes unchanged. Source audits used mode=ro. Follow-up remains in preview, no production activation.
