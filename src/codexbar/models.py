from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = 1


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class ProfileIdentity:
    account_id: str = ""
    plan_type: str = ""
    org_id: str = ""
    org_title: str = ""
    identity_source: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "account_id": self.account_id,
            "plan_type": self.plan_type,
            "org_id": self.org_id,
            "org_title": self.org_title,
            "identity_source": self.identity_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileIdentity":
        return cls(
            account_id=str(data.get("account_id", "")),
            plan_type=str(data.get("plan_type", "")),
            org_id=str(data.get("org_id", "")),
            org_title=str(data.get("org_title", "")),
            identity_source=str(data.get("identity_source", "")),
        )

    def summary(self) -> str:
        pieces = [piece for piece in [self.plan_type, self.org_title, self.account_id] if piece]
        return " / ".join(pieces)

    def is_empty(self) -> bool:
        return not any([self.account_id, self.plan_type, self.org_id, self.org_title])

    def matches(self, other: "ProfileIdentity") -> bool:
        if self.is_empty() or other.is_empty():
            return False
        if self.account_id and other.account_id:
            return self.account_id == other.account_id
        if self.org_id and other.org_id:
            return self.org_id == other.org_id
        return bool(
            self.plan_type
            and other.plan_type
            and self.org_title
            and other.org_title
            and self.plan_type == other.plan_type
            and self.org_title == other.org_title
        )


@dataclass
class ProfileMeta:
    name: str
    description: str = ""
    provider: str = ""
    managed_paths: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_timestamp)
    updated_at: str = field(default_factory=utc_timestamp)
    last_captured_at: str = field(default_factory=utc_timestamp)
    account_id: str = ""
    plan_type: str = ""
    org_id: str = ""
    org_title: str = ""
    identity_source: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "description": self.description,
            "provider": self.provider,
            "managed_paths": list(self.managed_paths),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_captured_at": self.last_captured_at,
            "account_id": self.account_id,
            "plan_type": self.plan_type,
            "org_id": self.org_id,
            "org_title": self.org_title,
            "identity_source": self.identity_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileMeta":
        managed = [str(item) for item in data.get("managed_paths", [])]
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            provider=str(data.get("provider", "")),
            managed_paths=managed,
            created_at=str(data.get("created_at", utc_timestamp())),
            updated_at=str(data.get("updated_at", utc_timestamp())),
            last_captured_at=str(data.get("last_captured_at", data.get("updated_at", utc_timestamp()))),
            account_id=str(data.get("account_id", "")),
            plan_type=str(data.get("plan_type", "")),
            org_id=str(data.get("org_id", "")),
            org_title=str(data.get("org_title", "")),
            identity_source=str(data.get("identity_source", "")),
        )

    @property
    def identity(self) -> ProfileIdentity:
        return ProfileIdentity(
            account_id=self.account_id,
            plan_type=self.plan_type,
            org_id=self.org_id,
            org_title=self.org_title,
            identity_source=self.identity_source,
        )


@dataclass
class SwitchResult:
    from_profile: str | None
    to_profile: str
    snapshot_id: str
    changed_paths: list[str]
    dry_run: bool = False


@dataclass
class ImportSummary:
    imported: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
