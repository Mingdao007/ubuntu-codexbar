# ubuntu-codexbar

A public Codex skill plus the current `codexbar` Python CLI for managing saved
Codex login profiles and usage on Ubuntu.

Chinese mirror: [README.zh-CN.md](README.zh-CN.md)

## Validated Baseline

| Component | Status |
|-----------|--------|
| Ubuntu | `22.04.5 LTS` |
| Python | `3.10.12` |
| CLI | `codexbar` with `init`, `capture`, `whoami`, `usage`, `usage --all`, `doctor` |
| Local data model | `~/.codex` + `~/.codexbar` saved-profile workflow |

## Problems Covered

- Need to capture and manage multiple saved Codex login profiles under `~/.codexbar/profiles`
- Need to switch or inspect accounts without confusing saved `active_profile` with the current root auth on disk
- Need to inspect local usage, cached per-profile usage, and current-root live usage from local session logs
- Need to explain duplicate saved identities, stale state, and canonical profile behavior on Ubuntu

## What Ships

- `ubuntu-codexbar/`: installable Codex skill package
- `src/codexbar/`: Python CLI implementation
- `tests/`: pytest coverage for switching, identity, and usage logic
- `pyproject.toml` + `setup.py`: installable local package metadata

## Install

1. Install the CLI from this repository:

```bash
python3 -m pip install --user --no-deps --no-build-isolation .
```

2. Copy `ubuntu-codexbar/` into `${CODEX_HOME:-$HOME/.codex}/skills/`.
3. Restart Codex or refresh local skills.
4. Invoke the skill as `$ubuntu-codexbar`.

If you only want the CLI, you can skip the skill install.

## Quick Start

```bash
codexbar init
codexbar capture main --overwrite
codexbar whoami
codexbar usage
codexbar usage --all
codexbar usage --all --refresh
codexbar doctor
```

## Attribution

This repository borrows ideas and workflow inspiration from:

- [`isxlan0/Codex_AccountSwitch`](https://github.com/isxlan0/Codex_AccountSwitch)
- [`lizhelang/codexbar`](https://github.com/lizhelang/codexbar)

The Ubuntu-oriented skill packaging, current Python implementation, and public
repository layout here are maintained in this repository.

## Privacy Boundary

This repository ships portable code, tests, and skill documentation only. It
does not include private `~/.codex` data, saved auth payloads, local session
logs, or personal machine state.

## Repository Layout

- `ubuntu-codexbar/`: Codex skill package
- `src/`: Python package source
- `tests/`: automated tests
- `README.md`: English overview
- `README.zh-CN.md`: Chinese overview
- `LICENSE`: MIT license
