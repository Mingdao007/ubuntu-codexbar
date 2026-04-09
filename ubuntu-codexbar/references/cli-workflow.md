# CLI Workflow

Use this reference when the user needs the exact command to run next.

## Install Or Verify

From the repository root:

```bash
python3 -m pip install --user --no-deps --no-build-isolation -e .
codexbar --help
```

## Identity And State

Use these first:

```bash
codexbar whoami
codexbar doctor
codexbar list
```

- `whoami` explains the saved active profile, the current root identity, and the
  canonical saved profile for that identity.
- `doctor` is useful when the user suspects `~/.codex` or `~/.codexbar` drift.

## Save Or Update Profiles

```bash
codexbar init
codexbar create work
codexbar capture work --overwrite
codexbar validate work
```

- Prefer `capture <name> --overwrite` when the login on disk changed and the
  saved profile should be updated.
- Use `validate` before switching if payload integrity is questionable.

## Switch Profiles

```bash
codexbar activate work
codexbar switch work
```

- `activate` and `switch` both apply the target saved payload to the root
  `~/.codex` state.
- Expect snapshot and rollback behavior to live under `~/.codexbar/snapshots`.

## Usage

```bash
codexbar usage
codexbar usage --all
codexbar usage --all --refresh
codexbar usage --history --days 30
```

- `usage` focuses on the latest current-root local session view.
- `usage --all` shows saved per-profile session snapshots and current-root context.
- `usage --all --refresh` is a deprecated compatibility form; it does not trigger live probing in the current CLI.
