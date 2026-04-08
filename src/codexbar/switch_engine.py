from __future__ import annotations

import subprocess
from pathlib import Path

from .auth_identity import read_profile_identity
from .fs_utils import ExclusiveFileLock, copy_if_exists, remove_path
from .models import SwitchResult, utc_timestamp
from .paths import AppPaths
from .profile_store import ProfileStore


def is_codex_process_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-fi", "codex"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


class SwitchEngine:
    def __init__(self, paths: AppPaths, store: ProfileStore):
        self.paths = paths
        self.store = store

    def switch(self, target_profile: str, allow_running: bool = False, dry_run: bool = False) -> SwitchResult:
        self.store.ensure_layout()
        target_meta = self.store.get_profile(target_profile)
        if "auth.json" not in target_meta.managed_paths:
            raise ValueError(f"Profile '{target_profile}' is invalid: missing auth.json")

        if not allow_running and is_codex_process_running():
            raise RuntimeError("Codex process appears to be running. Close it or pass --allow-running.")

        active_profile = self._current_root_profile_name()
        active_meta = self.store.get_profile(active_profile) if active_profile else None

        managed_set: list[str] = []
        seen: set[str] = set()
        for rel in (active_meta.managed_paths if active_meta else []):
            if rel not in seen:
                managed_set.append(rel)
                seen.add(rel)
        for rel in target_meta.managed_paths:
            if rel not in seen:
                managed_set.append(rel)
                seen.add(rel)

        snapshot_id = f"{utc_timestamp().replace(':', '').replace('-', '')}-{target_profile}"
        if dry_run:
            return SwitchResult(
                from_profile=active_profile,
                to_profile=target_profile,
                snapshot_id=snapshot_id,
                changed_paths=managed_set,
                dry_run=True,
            )

        with ExclusiveFileLock(self.paths.lock_file):
            # Reload active profile after lock acquisition to avoid stale state.
            active_profile = self._current_root_profile_name()
            active_meta = self.store.get_profile(active_profile) if active_profile else None

            managed_set = []
            seen.clear()
            for rel in (active_meta.managed_paths if active_meta else []):
                if rel not in seen:
                    managed_set.append(rel)
                    seen.add(rel)
            for rel in target_meta.managed_paths:
                if rel not in seen:
                    managed_set.append(rel)
                    seen.add(rel)

            snapshot_dir = self.paths.snapshots_root / snapshot_id
            self._snapshot_root_state(snapshot_dir, managed_set)

            if active_profile:
                self.store.sync_profile_from_root(active_profile, self.paths.codex_home)

            try:
                self._apply_target_profile(target_profile, target_meta.managed_paths, managed_set)
            except Exception:
                self._restore_root_state(snapshot_dir, managed_set)
                raise

            self.store.set_active_profile(target_profile)
            self.store.append_audit(
                {
                    "event": "switch",
                    "timestamp": utc_timestamp(),
                    "from": active_profile,
                    "to": target_profile,
                    "snapshot_id": snapshot_id,
                    "managed_paths": managed_set,
                }
            )

        return SwitchResult(
            from_profile=active_profile,
            to_profile=target_profile,
            snapshot_id=snapshot_id,
            changed_paths=managed_set,
            dry_run=False,
        )

    def _current_root_profile_name(self) -> str | None:
        root_identity = read_profile_identity(self.paths.codex_home / "auth.json")
        canonical = self.store.canonical_profile_for_identity(root_identity)
        if canonical is not None:
            return canonical.name
        return self.store.active_profile_name()

    def _snapshot_root_state(self, snapshot_dir: Path, managed_paths: list[str]) -> None:
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        for rel in managed_paths:
            copy_if_exists(self.paths.codex_home / rel, snapshot_dir / rel)

    def _restore_root_state(self, snapshot_dir: Path, managed_paths: list[str]) -> None:
        for rel in managed_paths:
            src = snapshot_dir / rel
            dst = self.paths.codex_home / rel
            if src.exists() or src.is_symlink():
                copy_if_exists(src, dst)
            else:
                remove_path(dst)

    def _apply_target_profile(self, target_profile: str, target_managed: list[str], managed_union: list[str]) -> None:
        target_set = set(target_managed)
        for rel in managed_union:
            dst = self.paths.codex_home / rel
            if rel in target_set:
                src = self.store.payload_path(target_profile, rel)
                if not src.exists() and not src.is_symlink():
                    raise FileNotFoundError(f"Target payload missing: {rel}")
                copy_if_exists(src, dst)
            else:
                remove_path(dst)
