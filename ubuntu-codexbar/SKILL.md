---
name: ubuntu-codexbar
description: |
  Use when managing or debugging saved Codex login profiles on Ubuntu with the
  local `codexbar` CLI. Covers profile capture and switching, `active_profile`
  vs current root auth mismatches, duplicate saved identities, and `usage`,
  `usage --all`, `usage --all --refresh`, `whoami`, `doctor`, and `validate`
  workflows under `~/.codex` and `~/.codexbar`.
---

# Ubuntu Codexbar

Use this skill when the user wants to work with saved Codex login profiles on
Ubuntu rather than treating `~/.codex/auth.json` as the only state.

This skill assumes the local `codexbar` CLI from this repository is available,
or can be installed first from the repository root.

## Quick Start

Check the current login state first:

```bash
codexbar whoami
codexbar doctor
```

Inspect local usage state:

```bash
codexbar usage
codexbar usage --all
```

Refresh canonical saved-profile usage only when needed:

```bash
codexbar usage --all --refresh
```

Capture the current auth into a saved profile:

```bash
codexbar capture main --overwrite
```

## Workflow

1. Confirm the CLI is available with `codexbar --help`.
2. Use `whoami` and `doctor` before telling the user which account is really active.
3. Use `list`, `create`, `capture`, `activate`, and `switch` to manage saved profiles.
4. Use `usage` for the current-root local session view.
5. Use `usage --all` for cached per-profile snapshots.
6. Use `usage --all --refresh` only when the user wants live probing of canonical saved profiles.
7. Use `validate` before switching if a saved profile looks incomplete or inconsistent.

## Rules

- Treat the current root auth on disk as the live truth.
- Treat saved `active_profile` as state, not guaranteed live identity.
- Explain cached vs live usage explicitly when the two differ.
- Expect canonical-profile suppression when two saved names map to the same identity.
- Use `--refresh` sparingly because it probes each canonical profile in a scratch `CODEX_HOME`.
- Keep shared session history under `~/.codex/sessions`; do not invent per-profile session silos unless the user explicitly wants a different design.

## Reference

Read [references/cli-workflow.md](references/cli-workflow.md) for command
selection and [references/state-model.md](references/state-model.md) for how
`active_profile`, current root auth, canonical profiles, and cached usage fit
together.
