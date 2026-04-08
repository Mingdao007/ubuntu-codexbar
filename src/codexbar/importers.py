from __future__ import annotations

from pathlib import Path

from .models import ImportSummary
from .paths import LEGACY_IMPORT_IGNORED_DIRS, LEGACY_IMPORT_IGNORED_ENTRIES
from .profile_store import ProfileStore


def _candidate_profile_dirs(source_root: Path) -> list[Path]:
    if not source_root.is_dir():
        return []
    result: list[Path] = []
    for path in sorted(source_root.iterdir(), key=lambda p: p.name):
        if not path.is_dir():
            continue
        if path.name.startswith("."):
            continue
        if path.name in LEGACY_IMPORT_IGNORED_DIRS:
            continue
        result.append(path)
    return result


def _collect_managed_paths(directory: Path) -> list[str]:
    managed: list[str] = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if entry.name in LEGACY_IMPORT_IGNORED_ENTRIES:
            continue
        managed.append(entry.name)
    return managed


def import_legacy_account_backup(
    store: ProfileStore,
    source_root: Path,
    prefix: str = "legacy-",
    overwrite: bool = False,
) -> ImportSummary:
    summary = ImportSummary()
    for src_profile_dir in _candidate_profile_dirs(source_root):
        target_name = f"{prefix}{src_profile_dir.name}" if prefix else src_profile_dir.name

        if store.profile_exists(target_name):
            if overwrite:
                summary.failed.append(f"{target_name}: overwrite is not implemented yet")
            else:
                summary.skipped.append(f"{target_name}: profile already exists")
            continue

        managed_paths = _collect_managed_paths(src_profile_dir)
        if "auth.json" not in managed_paths:
            summary.skipped.append(f"{target_name}: missing auth.json")
            continue

        try:
            store.create_profile_from_directory(
                name=target_name,
                source_dir=src_profile_dir,
                managed_paths=managed_paths,
                description=f"Imported from {src_profile_dir}",
                provider="imported",
            )
        except Exception as exc:
            summary.failed.append(f"{target_name}: {exc}")
            continue

        summary.imported.append(target_name)

    return summary
