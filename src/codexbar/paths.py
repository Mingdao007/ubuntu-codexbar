from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MANAGED_PATHS: tuple[str, ...] = ("auth.json", "config.toml")
LEGACY_IMPORT_IGNORED_DIRS: set[str] = {"_autosave", "windows"}
LEGACY_IMPORT_IGNORED_ENTRIES: set[str] = {".active_profile", ".DS_Store", ".current_profile"}


@dataclass(frozen=True)
class AppPaths:
    codex_home: Path
    codexbar_home: Path

    @classmethod
    def from_env(cls) -> "AppPaths":
        codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
        codexbar_home = Path(os.environ.get("CODEXBAR_HOME", "~/.codexbar")).expanduser()
        return cls(codex_home=codex_home, codexbar_home=codexbar_home)

    @property
    def profiles_root(self) -> Path:
        return self.codexbar_home / "profiles"

    @property
    def snapshots_root(self) -> Path:
        return self.codexbar_home / "snapshots"

    @property
    def logs_root(self) -> Path:
        return self.codexbar_home / "logs"

    @property
    def audit_log_file(self) -> Path:
        return self.logs_root / "audit.jsonl"

    @property
    def state_file(self) -> Path:
        return self.codexbar_home / "state.json"

    @property
    def lock_file(self) -> Path:
        return self.codexbar_home / ".switch.lock"

    @property
    def legacy_backup_root(self) -> Path:
        return self.codex_home / "account_backup"
