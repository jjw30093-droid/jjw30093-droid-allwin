# PDF reading and evidence exports

Scope: all-win isolated release worktree; no production deployment. Existing data-quality changes in this worktree preserved.

Default PDF now emits a reading report only. `pdf_profile=evidence` produces a separate evidence document using the same immutable report/editorial revision. Existing JSON export remains full evidence. The UI has separate buttons, and OpenAPI/TypeScript are regenerated from Pydantic.

Reading sections are grouped by question; recurring xA ranks, duplicated region maps, shot indexes and technical appendices are not appended again. Main body10pt, captions9pt versus previous8.5/7.5; pitch scale unchanged. All actual goals plus selected high-xG non-goals retained postmatch; prematch shows six representative chance chains, with full IDs in evidence/JSON. Unselected pair charts are omitted.

Real fixtures: production Sunderland-Chelsea prematch6c22ce98-6712-4bc0-b828-5efe86608030 and linked postmatchd61400b8-1a6d-4b18-92d0-da87b0107032, same stored snapshots as prior152/122page exports. New reader26/23pages. Separate evidence251/128pages; those are intentionally full event/source indexes, not additional reading chapters. Five saved prematch checks remain in the postmatch report. Legacy EPL comparison can be projected only from saved all-match intervals which reconcile to recorded minutes and a saved regular-time11v11/non-penalty scope; original input never mutated.

Verified:26 focused backend tests;4 download tests including separate profile payload;scoped TypeScript and ESLint; real localhost admin-fixture POST export/download200 and PDF signature for both profiles; all reader pages rendered, blank overflow page repaired, final text checks reject blank pages/UUIDs/unselected-pair placeholders. Native IAB PDF viewer is blank, so local preview displays exact1400px rendered PDF pages with chapter/page controls and links to originals; it does not redraw analytical figures.

Current full Next build is not newly certified: earlier font-loader/Turbopack environment failures remain documented in cc-standard-quality-20261110.md. This local PDF delivery is not a production release claim.

Artifacts: output/pdf/cc-{prematch,postmatch}-{reader,evidence}-20261110.pdf under the chat visualization workspace. QA/proof: /tmp/cc-pdf-reader-final-proof.json.

Final: reading profiles26/23pages; no blank pages, event UUIDs or unselected pair placeholders. Common first-page source notes and review criteria printed once. Both final report PDFs opened in the local exact-page preview, including lineup and saved-five-judgment review chapters.
