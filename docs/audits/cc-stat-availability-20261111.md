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


## Production release accepted - 2026-11-11 (supersedes preview-only status)

- Git commits: website a9a426037236; source 56c3f11. Both pushed before deployment. Website current=/opt/allwin/releases/a9a426037236; CC analyst current=/home/ubuntu/opta-data/analyst_api/releases/56c3f11. Collector runtime/cadence unchanged.
- Normal release script completed exit0, full next build and TypeScript passed,24/24 static pages generated, bundle/public-URL/candidate/live/business gates passed. All three business databases backed up with integrity_check=ok (backup set20261110T171911Z). No new schema migration. Backup checks were slow; temporary per-process I/O priority and read-only cache warming accelerated them, with no integrity gate skipped.
- Cloudflare targeted purge succeeded for CC and VIP Studio paths. CC Nginx18300/18800 tunnels point to the deployed website/API; loopback HTML200, anonymous report API401 and private/no-store. Public curl from CC host receives a Cloudflare managed403 challenge; actual authenticated Chrome page and export were verified, no WAF bypass or permission changes.
- Actual authenticated admin UI enqueued job8c36a635-31c1-43c7-bd16-95af049539e6. Normal production worker processed it successfully using workflowv8. New draft9d273eab-4801-4ae5-a401-4a48c7ab2d9b is pending review, previous draftad9a198f-a9e7-44ad-92d9-eac6f827ec0d retained. Regular analyst worker restarted and active.
- Production snapshot checks: Maguire totalCross=0,zero_omitted/played; Alysson interception=2,reported/played; de Ligt totalPass=null,not_played. Actual XI11+11, reconstructed historical samples5+5, ready_for_review. Real page shows zero counts, unused players and snapshot-unlinked MA32 score labels separately.
- Reader export8b962a63-c42d-42ef-a909-82e821e238db created by actual browser UI, status succeeded; Chrome download completed293KB. Production reader26 pages,299615 bytes, no blank pages. Normalized extracted text verifies zero-restoration note, unused-player labels and unlinked-does-not-mean-CC-source-absent explanation. Rendered changed pages5/6 inspected: zero counts and Alysson2 interceptions reconcile, no clipping observed.
- PDF SHA256caa956520d69170950647609a98c00f2a8bdaf8f6d6cf456434f33fb20273e8c. Browser file: output/pdf/cc-man-villa-production-stat-20261111.pdf in chat visualization workspace. Download screenshot:/tmp/cc-stat-production-download-20261111.png.

Production URL:https://miaomiaodi.cc/studio/reports?report=9d273eab-4801-4ae5-a401-4a48c7ab2d9b#report-roles
