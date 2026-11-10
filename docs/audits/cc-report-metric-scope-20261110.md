# Analyst report metric and coverage inventory

|Metric group|Source / grain|Scope and denominator|Missing policy|
|---|---|---|---|
|Shots, xG, shot outcome, penalties|CC normalized event+shot+Q9|Same selected period/state/manpower, npxG excludes all Q9 attempts|Own goals separately; unknown xG stays null; coverage count per sample|
|xA total/open play/set play|Opta official player-match|Full match only; never divided across event regions/time|No xA fact is unknown; known zero remains zero|
|Corners and official count stats|CC full-match official team stats|All covered matches, including contract-authorized sparse zeros|Only semantic_contract missing_is_zero AND raw stat list present; absent table/feed unknown|
|League comparison|Same CC official stats|Current season before cutoff; covered team-match denominators per metric|Missing games excluded and coverage shown; no averaging match percentages|
|Progression, event zones|CC normalized selected events|Same event period/state/manpower, same minutes for per90|Missing coordinates kept outside allocated zones; never infer movement tracking|
|Chance chain|CC explicit sequence/event links|Recorded linked actions only; Q55 v3 creator links preserved|No invented passes; unresolved links remain unassigned|
|xT / PV|Existing frozen models / Opta event values|Selected identities, preserved source/model versions|Unscored action unknown; no training on report generation|
|Roster and lineup|Actual CC Opta XI; identity-verified VIP schematic only|One-to-one team/roster link; actual Opta shirt numbers retained|Identity unresolved vs matched/no recent sample distinct; unlocated names shown|
|Regional comparison|Saved prematch snapshot or kickoff-bounded CC reconstruction|Regular time, 11v11, nonpenalty; current and history same scope|Unavailable/incomparable is not zero; reconstructed history is not a prior prediction|
|Pair and on-pitch intervals|CC player/state/period intervals|Four groups; same minutes/state; sample under3 games/180min marked|No sample not equal zero; ambiguous lineups block dependent judgment|
|Aerials/contests/set pieces|CC paired source events and qualifiers|Deduplicated linked samples; first and second phases distinct|Coverage shown; no inferred marking responsibility|
|Keeper / network|CC shots xGOT / full match pass summary|xGOT-covered on-target shots; network full match|Not tracking positions or interval network; unknown not zero|

Rendering contract: report page, reading PDF, evidence PDF and JSON read the same immutable report snapshot and quality payload. Technical IDs/receipts remain in independent evidence output; no manual numerical corrections. New source/definition versions generate new pending drafts, preserve reviewed text/history.
