---
name: ubuntu-codexbar
description: |
  Use when managing or debugging saved Codex login profiles on Ubuntu with the
  local `codexbar` CLI. Covers profile capture and switching, `active_profile`
  vs current root auth mismatches, duplicate saved identities, current-root
  usage, saved per-profile usage snapshots, and `usage`, `usage --all`,
  `whoami`, `doctor`, and `validate` workflows under `~/.codex` and
  `~/.codexbar`.
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

Legacy compatibility flags are still accepted, but they do not trigger live
saved-profile probing in the current CLI:

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
5. Use `usage --all` for saved per-profile session snapshots plus current-root context.
6. Treat `usage --all --refresh` and `--timeout` as deprecated compatibility flags, not live probing controls.
7. Use `validate` before switching if a saved profile looks incomplete or inconsistent.

## Rules

- Treat the current root auth on disk as the live truth.
- Treat saved `active_profile` as state, not guaranteed live identity.
- Explain current-root session snapshots vs saved per-profile snapshots explicitly.
- Expect canonical-profile suppression when two saved names map to the same identity.
- Do not promise that `--refresh` probes canonical saved profiles in the current CLI.
- Keep shared session history under `~/.codex/sessions`; do not invent per-profile session silos unless the user explicitly wants a different design.

## Reference

Read [references/cli-workflow.md](references/cli-workflow.md) for command
selection and [references/state-model.md](references/state-model.md) for how
`active_profile`, current root auth, canonical profiles, and cached usage fit
together.
