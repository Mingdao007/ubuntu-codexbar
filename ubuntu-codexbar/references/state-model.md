# State Model

Use this reference when the user is confused by profile names, root auth, or
usage rows that do not seem to match.

## Current Root Auth

The live login is whatever identity is currently present in:

- `~/.codex/auth.json`

This is the source of truth for the current root identity.

## Saved Active Profile

The saved active profile lives in:

- `~/.codexbar/state.json`

It is useful state, but it can become stale. Do not assume it matches the live
login without checking `whoami` or `doctor`.

## Canonical Profile

If multiple saved profile names resolve to the same account identity, `codexbar`
chooses one canonical saved profile.

- canonical profile rows keep the usage/cache view
- duplicate rows are suppressed rather than pretending they are separate accounts

## Usage Semantics

- current-root live usage comes from the latest local session log snapshot
- per-profile rows in `usage --all` are cached snapshots unless explicitly refreshed
- if saved active profile and current root profile differ, explain the mismatch
  directly instead of treating the saved state as live truth
