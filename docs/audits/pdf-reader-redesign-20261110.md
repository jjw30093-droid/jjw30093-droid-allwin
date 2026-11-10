# PDF reading and evidence exports

Scope: all-win isolated release worktree; no production deployment. Existing data-quality changes in this worktree preserved.

Default PDF now emits a reading report only. `pdf_profile=evidence` produces a separate evidence document using the same immutable report/editorial revision. Existing JSON export remains full evidence. The UI has separate buttons, and OpenAPI/TypeScript are regenerated from Pydantic.

Reading sections are grouped by question; recurring xA ranks, duplicated region maps, shot indexes and technical appendices are not appended again. Main body10pt, captions9pt versus previous8.5/7.5; pitch scale unchanged. All actual goals plus selected high-xG non-goals retained postmatch; prematch shows six representative chance chains, with full IDs in evidence/JSON. Unselected pair charts are omitted.

Real fixtures: production Sunderland-Chelsea prematch6c22ce98-6712-4bc0-b828-5efe86608030 and linked postmatchd61400b8-1a6d-4b18-92d0-da87b0107032, same stored snapshots as prior152/122page exports. New reader26/23pages. Separate evidence251/128pages; those are intentionally full event/source indexes, not additional reading chapters. Five saved prematch checks remain in the postmatch report. Legacy EPL comparison can be projected only from saved all-match intervals which reconcile to recorded minutes and a saved regular-time11v11/non-penalty scope; original input never mutated.

Verified:26 focused backend tests;4 download tests including separate profile payload;scoped TypeScript and ESLint; real localhost admin-fixture POST export/download200 and PDF signature for both profiles; all reader pages rendered, blank overflow page repaired, final text checks reject blank pages/UUIDs/unselected-pair placeholders. Native IAB PDF viewer is blank, so local preview displays exact1400px rendered PDF pages with chapter/page controls and links to originals; it does not redraw analytical figures.

Current full Next build is not newly certified: earlier font-loader/Turbopack environment failures remain documented in cc-standard-quality-20261110.md. This local PDF delivery is not a production release claim.

Artifacts: output/pdf/cc-{prematch,postmatch}-{reader,evidence}-20261110.pdf under the chat visualization workspace. QA/proof: /tmp/cc-pdf-reader-final-proof.json.

Final: reading profiles26/23pages; no blank pages, event UUIDs or unselected pair placeholders. Common first-page source notes and review criteria printed once. Both final report PDFs opened in the local exact-page preview, including lineup and saved-five-judgment review chapters.


## Production acceptance — 2026-11-11 (supersedes preparation status above)

Status: deployed and accepted for the checks described here. Website release `59ea209b033e`; CC analyst API `da76530`; preserved collector runtime `67397b8`, pinned by Git configuration `1574257`. Release used Git, database backups/integrity checks, full production build, atomic activation, service health/readiness and public asset gates. Cloudflare targeted purge succeeded for both domains. Anonymous administrator report access remains 401 with private/no-store caching. Existing prediction service and collector cadence were preserved.

- Applied 131 derived penalty-flag corrections across 126 matches under the existing single-writer lock. Raw match source hashes reconciled with the fsynced rollback checkpoint. No raw payload rewrite, model training or full database download.
- After repair: 2,521/2,521 current-season matches passed the bounded audit, with no remaining detected issues. EPL independent core reconciliation covered 100 matches / 172,356 events. This certifies the implemented identity, interval, score, shot/xG and penalty checks, not every supplier field or every tactical association.
- Crystal Palace last-five corner counts [4,0,7,5,5] reconcile to 4.20, including the legal zero sample.
- Production Manchester United–Aston Villa draft `ad9a198f-a9e7-44ad-92d9-eac6f827ec0d`: actual XI and located players 11/11 each, Alysson present; regional history five matches each, explicitly reconstructed with kickoff-bounded scope. Real authenticated browser and 440px mobile emulation checked lineup and regional charts; no page-wide overflow. Device emulation restored afterwards.
- Sunderland–Chelsea prematch `8674c76d-c249-46d6-87e7-5f4c44f968fb`, postmatch `68bdd1bf-f4e6-4387-a887-48c5341d5d04`, and Forest–Manchester City postmatch `8f2c1d56-0ce8-41b7-b53b-0e1e76ffec77` generated from production source data. New versions remain pending review; prior reviewed snapshots and manual edits were not overwritten.
- Three production reader PDFs (Manchester United–Villa 25 pages, Sunderland–Chelsea 23, Forest–City 24) rendered and inspected across all 72 pages; zero blank pages and zero full event UUIDs in reader text. Lineup and reconstructed baseline pages inspected individually. Real authenticated Chrome UI downloads completed for both reading PDF (289 KB) and independent evidence PDF (448 KB).
- Source tests: 80 passed. Website focused quality/PDF suite previously 56 passed, latest affected workflow/PDF/prematch checks 27 passed; frontend export regressions 4 passed and scoped ESLint passed. Isolated full webpack build and production native build both passed, including TypeScript and 24 static pages. Font-loader blocker resolved with licensed self-hosted subsets, unchanged font families.

Evidence: `/tmp/cc-standard-quality-after-20261110.json`; server `/home/ubuntu/opta-data/reports/quality-current-20261110-after.json`; rollback checkpoint `/home/ubuntu/opta-data/reports/penalty-q9-before-20261110.json`; server report exports `/opt/allwin/shared/data/exports/quality-20261110/acceptance.json`; browser proof `/tmp/cc-production-pdf-download-proof.png`; mobile proof `/tmp/cc-production-mobile-{lineup,matchups}.png`. These local paths are validation artifacts, not runtime dependencies.

Limitations: optional missing supplier metrics remain explicitly unavailable; reconstructed history is not an original saved pre-match prediction. Tracking/marking responsibility still requires video. No claim is made that all provider fields or all historic seasons have been certified.
