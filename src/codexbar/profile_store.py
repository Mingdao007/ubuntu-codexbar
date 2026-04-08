from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Iterable

from .auth_identity import read_profile_identity
from .fs_utils import copy_if_exists, ensure_private_dir, read_json, remove_path, write_json_atomic
from .models import ProfileMeta, utc_timestamp
from .paths import AppPaths, DEFAULT_MANAGED_PATHS
from .usage_stats import RateLimitSnapshot


class ProfileStore:
    def __init__(self, paths: AppPaths):
        self.paths = paths

    def ensure_layout(self) -> None:
        ensure_private_dir(self.paths.codexbar_home)
        ensure_private_dir(self.paths.profiles_root)
        ensure_private_dir(self.paths.snapshots_root)
        ensure_private_dir(self.paths.logs_root)
        if not self.paths.state_file.exists():
            self._write_state(
                {
                    "schema_version": 1,
                    "active_profile": None,
                    "session_label": None,
                    "updated_at": utc_timestamp(),
                }
            )

    def list_profiles(self) -> list[ProfileMeta]:
        self.ensure_layout()
        profiles: list[ProfileMeta] = []
        for directory in sorted(self.paths.profiles_root.iterdir(), key=lambda p: p.name):
            if not directory.is_dir():
                continue
            meta_path = self._meta_path(directory.name)
            if not meta_path.is_file():
                continue
            profiles.append(self._hydrate_profile_identity(ProfileMeta.from_dict(read_json(meta_path))))
        return profiles

    def get_profile(self, name: str) -> ProfileMeta:
        meta_path = self._meta_path(name)
        if not meta_path.is_file():
            raise FileNotFoundError(f"Profile not found: {name}")
        return self._hydrate_profile_identity(ProfileMeta.from_dict(read_json(meta_path)))

    def profile_exists(self, name: str) -> bool:
        return self._meta_path(name).is_file()

    def active_profile_name(self) -> str | None:
        self.ensure_layout()
        state = self._read_state()
        active = state.get("active_profile")
        if not active:
            return None
        if not self.profile_exists(str(active)):
            return None
        return str(active)

    def set_active_profile(self, profile_name: str | None) -> None:
        self.ensure_layout()
        state = self._read_state()
        state["active_profile"] = profile_name
        state["updated_at"] = utc_timestamp()
        self._write_state(state)

    def session_label(self) -> str | None:
        self.ensure_layout()
        state = self._read_state()
        label = state.get("session_label")
        if not label:
            return None
        if not self.profile_exists(str(label)):
            return None
        return str(label)

    def set_session_label(self, profile_name: str | None) -> None:
        self.ensure_layout()
        state = self._read_state()
        state["session_label"] = profile_name
        state["updated_at"] = utc_timestamp()
        self._write_state(state)

    def state_snapshot(self) -> dict[str, object]:
        self.ensure_layout()
        state = self._read_state()
        return {
            "schema_version": state.get("schema_version", 1),
            "active_profile": self.active_profile_name(),
            "session_label": self.session_label(),
            "updated_at": state.get("updated_at"),
        }

    def create_profile_from_root(
        self,
        name: str,
        source_root: Path,
        managed_paths: Iterable[str] | None = None,
        description: str = "",
        provider: str = "",
    ) -> ProfileMeta:
        return self._capture_profile(
            name=name,
            source_root=source_root,
            managed_paths=managed_paths or DEFAULT_MANAGED_PATHS,
            description=description,
            provider=provider,
            overwrite=False,
        )

    def capture_profile_from_root(
        self,
        name: str,
        source_root: Path,
        managed_paths: Iterable[str] | None = None,
        description: str | None = None,
        provider: str | None = None,
        overwrite: bool = False,
    ) -> ProfileMeta:
        existing = self.get_profile(name) if self.profile_exists(name) else None
        default_paths = existing.managed_paths if existing and managed_paths is None else (managed_paths or DEFAULT_MANAGED_PATHS)
        return self._capture_profile(
            name=name,
            source_root=source_root,
            managed_paths=default_paths,
            description=description,
            provider=provider,
            overwrite=overwrite,
        )

    def create_profile_from_directory(
        self,
        name: str,
        source_dir: Path,
        managed_paths: Iterable[str],
        description: str = "",
        provider: str = "",
    ) -> ProfileMeta:
        return self._capture_profile(
            name=name,
            source_root=source_dir,
            managed_paths=managed_paths,
            description=description,
            provider=provider,
            overwrite=False,
        )

    def sync_profile_from_root(self, name: str, source_root: Path) -> ProfileMeta:
        meta = self.get_profile(name)
        payload_dir = self._payload_dir(name)

        for rel in meta.managed_paths:
            src = source_root / rel
            dst = payload_dir / rel
            if src.exists() or src.is_symlink():
                copy_if_exists(src, dst)
            else:
                remove_path(dst)

        identity = read_profile_identity(payload_dir / "auth.json")
        meta.updated_at = utc_timestamp()
        meta.last_captured_at = meta.updated_at
        meta.account_id = identity.account_id
        meta.plan_type = identity.plan_type
        meta.org_id = identity.org_id
        meta.org_title = identity.org_title
        meta.identity_source = identity.identity_source
        self._write_meta(meta)
        return meta

    def payload_path(self, profile_name: str, rel_path: str) -> Path:
        return self._payload_dir(profile_name) / rel_path

    def quota_cache_path(self, profile_name: str) -> Path:
        return self._profile_dir(profile_name) / "quota_cache.json"

    def read_quota_cache(self, profile_name: str) -> RateLimitSnapshot | None:
        path = self.quota_cache_path(profile_name)
        if not path.is_file():
            return None
        data = read_json(path)
        if not data:
            return None
        try:
            return RateLimitSnapshot.from_dict(data)
        except Exception:
            return None

    def write_quota_cache(self, profile_name: str, snapshot: RateLimitSnapshot) -> None:
        if not self.profile_exists(profile_name):
            raise FileNotFoundError(f"Profile not found: {profile_name}")
        write_json_atomic(self.quota_cache_path(profile_name), snapshot.to_dict())

    def validate_profile(self, name: str) -> list[str]:
        errors: list[str] = []
        meta = self.get_profile(name)
        payload_dir = self._payload_dir(name)
        if not payload_dir.is_dir():
            errors.append("payload directory is missing")
            return errors

        if "auth.json" not in meta.managed_paths:
            errors.append("managed_paths does not contain auth.json")

        for rel in meta.managed_paths:
            if not (payload_dir / rel).exists():
                errors.append(f"missing payload entry: {rel}")

        return errors

    def append_audit(self, event: dict[str, object]) -> None:
        self.ensure_layout()
        self.paths.audit_log_file.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=True) + "\n"
        with self.paths.audit_log_file.open("a", encoding="utf-8") as f:
            f.write(line)

    def find_profiles_matching_identity(self, identity: object) -> list[ProfileMeta]:
        if not hasattr(identity, "is_empty") or identity.is_empty():
            return []
        return [profile for profile in self.list_profiles() if profile.identity.matches(identity)]

    def canonical_profile_for_identity(self, identity: object) -> ProfileMeta | None:
        matches = self.find_profiles_matching_identity(identity)
        if not matches:
            return None
        return self._canonical_profile(matches)

    def profile_relationships(self) -> dict[str, dict[str, object]]:
        relationships: dict[str, dict[str, object]] = {}
        for members in self._identity_groups(self.list_profiles()):
            canonical = self._canonical_profile(members)
            member_names = sorted(profile.name for profile in members)
            for profile in members:
                relationships[profile.name] = {
                    "canonical_name": canonical.name,
                    "duplicate_of": canonical.name if profile.name != canonical.name else None,
                    "is_canonical": profile.name == canonical.name,
                    "member_names": member_names,
                }
        return relationships

    def _normalize_paths(self, paths: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for raw in paths:
            value = str(raw).strip().strip("/")
            if not value:
                continue
            if value.startswith("../") or "/../" in value:
                continue
            if value in seen:
                continue
            normalized.append(value)
            seen.add(value)
        return normalized

    def _capture_profile(
        self,
        name: str,
        source_root: Path,
        managed_paths: Iterable[str],
        description: str | None,
        provider: str | None,
        overwrite: bool,
    ) -> ProfileMeta:
        self.ensure_layout()
        existing = self.get_profile(name) if self.profile_exists(name) else None
        if existing is not None and not overwrite:
            raise FileExistsError(f"Profile already exists: {name}")

        chosen_paths = self._normalize_paths(managed_paths)
        if not chosen_paths:
            raise ValueError("No valid managed paths were provided")

        staging_dir = Path(tempfile.mkdtemp(prefix=f".{name}.tmp-", dir=self.paths.profiles_root))
        try:
            payload_dir = staging_dir / "payload"
            payload_dir.mkdir(parents=True, exist_ok=False)

            copied: list[str] = []
            for rel in chosen_paths:
                if copy_if_exists(source_root / rel, payload_dir / rel):
                    copied.append(rel)

            if "auth.json" not in copied:
                raise ValueError("Cannot capture profile without auth.json in source root")

            now = utc_timestamp()
            identity = read_profile_identity(payload_dir / "auth.json")
            conflict_name = self._identity_conflict_name(name=name, identity=identity, existing=existing)
            if conflict_name is not None:
                raise ValueError(f"Identity already belongs to saved profile: {conflict_name}")
            meta = ProfileMeta(
                name=name,
                description=description if description is not None else (existing.description if existing else ""),
                provider=provider if provider is not None else (existing.provider if existing else ""),
                managed_paths=copied,
                created_at=existing.created_at if existing else now,
                updated_at=now,
                last_captured_at=now,
                account_id=identity.account_id,
                plan_type=identity.plan_type,
                org_id=identity.org_id,
                org_title=identity.org_title,
                identity_source=identity.identity_source,
            )
            write_json_atomic(staging_dir / "meta.json", meta.to_dict())

            target_dir = self._profile_dir(name)
            if existing is not None:
                remove_path(target_dir)
            staging_dir.rename(target_dir)
            return meta
        except Exception:
            remove_path(staging_dir)
            raise

    def _profile_dir(self, name: str) -> Path:
        return self.paths.profiles_root / name

    def _payload_dir(self, name: str) -> Path:
        return self._profile_dir(name) / "payload"

    def _meta_path(self, name: str) -> Path:
        return self._profile_dir(name) / "meta.json"

    def _write_meta(self, meta: ProfileMeta) -> None:
        write_json_atomic(self._meta_path(meta.name), meta.to_dict())

    def _hydrate_profile_identity(self, meta: ProfileMeta) -> ProfileMeta:
        if not meta.identity.is_empty():
            return meta

        identity = read_profile_identity(self._payload_dir(meta.name) / "auth.json")
        if identity.is_empty():
            return meta

        meta.account_id = identity.account_id
        meta.plan_type = identity.plan_type
        meta.org_id = identity.org_id
        meta.org_title = identity.org_title
        meta.identity_source = identity.identity_source
        self._write_meta(meta)
        return meta

    def _identity_conflict_name(self, name: str, identity: object, existing: ProfileMeta | None) -> str | None:
        if not hasattr(identity, "is_empty") or identity.is_empty():
            return None

        matches = self.find_profiles_matching_identity(identity)
        target_unchanged = existing is not None and existing.identity.matches(identity)

        if target_unchanged:
            canonical = self._canonical_profile(matches + ([existing] if existing.name not in {p.name for p in matches} else []))
            if canonical.name == name:
                return None
            return canonical.name

        other_matches = [profile for profile in matches if profile.name != name]
        if not other_matches:
            return None
        return self._canonical_profile(other_matches).name

    def _identity_groups(self, profiles: list[ProfileMeta]) -> list[list[ProfileMeta]]:
        groups: list[list[ProfileMeta]] = []
        account_groups: dict[str, list[ProfileMeta]] = {}
        fallback_profiles: list[ProfileMeta] = []

        for profile in profiles:
            if profile.identity.is_empty():
                groups.append([profile])
                continue
            if profile.account_id:
                account_groups.setdefault(profile.account_id, []).append(profile)
                continue
            fallback_profiles.append(profile)

        groups.extend(account_groups.values())

        for profile in fallback_profiles:
            attached = False
            for members in groups:
                if any(member.identity.matches(profile.identity) for member in members):
                    members.append(profile)
                    attached = True
                    break
            if attached:
                continue
            groups.append([profile])

        return [self._sorted_group_members(members) for members in groups]

    def _canonical_profile(self, profiles: list[ProfileMeta]) -> ProfileMeta:
        return self._sorted_group_members(profiles)[0]

    def _sorted_group_members(self, profiles: list[ProfileMeta]) -> list[ProfileMeta]:
        return sorted(profiles, key=lambda profile: (profile.created_at or "", profile.name))

    def _read_state(self) -> dict[str, object]:
        return read_json(
            self.paths.state_file,
            default={"schema_version": 1, "active_profile": None, "session_label": None},
        )

    def _write_state(self, state: dict[str, object]) -> None:
        write_json_atomic(self.paths.state_file, state)
