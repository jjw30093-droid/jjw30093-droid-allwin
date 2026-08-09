# Codex Repository Instructions

## Task startup

Every task must begin by fully reading:

1. `CLAUDE.md`
2. `docs/current-state.md`
3. The current module's relevant report under `docs/audits/`
4. The related source code and permanent tests

For files under `frontend/`, `frontend/AGENTS.md` remains the more specific rule. Do not delete or override it.

## Sources of truth

When evidence conflicts, use this priority:

1. Actual runtime evidence, database schema/state, source code, and tests
2. Long-term locked decisions in `CLAUDE.md`
3. `docs/current-state.md`
4. Historical audit reports and assistant summaries

Report conflicts explicitly. Do not select the most favorable account.

## Git and worktree protection

Treat all existing modified and untracked files as user assets. Before changing files, record the worktree, branch, HEAD, stash, and untracked-file state.

Do not run `git reset --hard`, `git checkout --`, `git clean`, automatic stash, unrelated cleanup, or whole-repository formatting. Keep patches minimal and preserve unrelated changes. Do not commit, push, tag, or deploy without explicit user authorization.

## Database protection

Real `data/*.db` files are read-only by default. Use SQLite `mode=ro` for audits and `immutable=1` when applicable. Mutation probes, fixtures, and tests must use `/tmp`, pytest temporary directories, or explicitly isolated copies. Never write DEMO, fixture, or test data into real databases.

Before and after relevant work, compare SHA-256, size, mtime, and WAL/SHM state for the real databases.

## Verification discipline

Do not claim completion without running the supporting command. If external network access, credentials, or a real service were not actually verified, mark the result `UNVERIFIED`. Offline fixtures do not prove live behavior.

A module is complete only after implementation, permanent tests, relevant regressions, `git diff --check`, database/worktree integrity checks, and documentation synchronization. Disclose every skip/xfail and do not use a broad `DONE` label to hide unverified work.

## Product and data integrity

Do not selectively delete failed predictions. Keep corrections, retractions, and supersessions transparent and traceable. Do not fabricate data coverage, model performance, source fields, endpoint capabilities, or external validation. Do not present probabilities as certain outcomes or describe contemporaneous events as causal evidence.

Dynamic test counts, database row counts, and temporary task progress belong in `docs/current-state.md` and actual command output, not this file.

Do not delete or rename `CLAUDE.md`.
