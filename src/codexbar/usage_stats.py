from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .paths import AppPaths


@dataclass
class SessionUsage:
    file_path: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "file_path": self.file_path,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class RateLimitWindow:
    used_percent: float
    remaining_percent: float
    window_minutes: int
    resets_at: int | None

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "used_percent": self.used_percent,
            "remaining_percent": self.remaining_percent,
            "window_minutes": self.window_minutes,
            "resets_at": self.resets_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "RateLimitWindow":
        used_percent = _as_float(data.get("used_percent"))
        remaining_percent = _as_float(data.get("remaining_percent"))
        if "remaining_percent" not in data:
            remaining_percent = max(0.0, 100.0 - used_percent)
        return cls(
            used_percent=used_percent,
            remaining_percent=remaining_percent,
            window_minutes=_as_int(data.get("window_minutes")),
            resets_at=_optional_int(data.get("resets_at")),
        )


@dataclass
class RateLimitSnapshot:
    observed_at: str
    source: str | None
    limit_id: str | None
    plan_type: str | None
    primary: RateLimitWindow | None
    secondary: RateLimitWindow | None

    def to_dict(self) -> dict[str, object]:
        return {
            "observed_at": self.observed_at,
            "source": self.source,
            "limit_id": self.limit_id,
            "plan_type": self.plan_type,
            "primary": self.primary.to_dict() if self.primary else None,
            "secondary": self.secondary.to_dict() if self.secondary else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "RateLimitSnapshot":
        primary = data.get("primary")
        secondary = data.get("secondary")
        return cls(
            observed_at=str(data.get("observed_at", "")),
            source=_string_or_none(data.get("source")),
            limit_id=_string_or_none(data.get("limit_id")),
            plan_type=_string_or_none(data.get("plan_type")),
            primary=RateLimitWindow.from_dict(primary) if isinstance(primary, dict) else None,
            secondary=RateLimitWindow.from_dict(secondary) if isinstance(secondary, dict) else None,
        )

    def with_source(self, source: str) -> "RateLimitSnapshot":
        return RateLimitSnapshot(
            observed_at=self.observed_at,
            source=source,
            limit_id=self.limit_id,
            plan_type=self.plan_type,
            primary=self.primary,
            secondary=self.secondary,
        )


@dataclass
class UsageSummary:
    scanned_files: int
    sessions_with_usage: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int
    top_sessions: list[SessionUsage]
    current_rate_limits: RateLimitSnapshot | None

    def to_dict(self) -> dict[str, object]:
        return {
            "scanned_files": self.scanned_files,
            "sessions_with_usage": self.sessions_with_usage,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_output_tokens": self.reasoning_output_tokens,
            "total_tokens": self.total_tokens,
            "top_sessions": [item.to_dict() for item in self.top_sessions],
            "current_rate_limits": self.current_rate_limits.to_dict() if self.current_rate_limits else None,
        }


def summarize_usage(paths: AppPaths, days: int | None = None, top: int = 5) -> UsageSummary:
    session_files = list(_iter_session_files(paths))
    if days is not None and days > 0:
        cutoff = time.time() - (days * 86400)
        session_files = [path for path in session_files if path.stat().st_mtime >= cutoff]

    per_session: list[SessionUsage] = []
    for path in session_files:
        usage = _parse_session_file(path)
        if usage is not None:
            per_session.append(usage)

    per_session.sort(key=lambda item: item.total_tokens, reverse=True)
    top_sessions = per_session[: max(0, top)]
    current_rate_limits = latest_rate_limits(paths, days=days, source="root-session")

    return UsageSummary(
        scanned_files=len(session_files),
        sessions_with_usage=len(per_session),
        input_tokens=sum(item.input_tokens for item in per_session),
        cached_input_tokens=sum(item.cached_input_tokens for item in per_session),
        output_tokens=sum(item.output_tokens for item in per_session),
        reasoning_output_tokens=sum(item.reasoning_output_tokens for item in per_session),
        total_tokens=sum(item.total_tokens for item in per_session),
        top_sessions=top_sessions,
        current_rate_limits=current_rate_limits,
    )


def latest_rate_limits(paths: AppPaths, days: int | None = None, source: str | None = None) -> RateLimitSnapshot | None:
    session_files = list(_iter_session_files(paths))
    if days is not None and days > 0:
        cutoff = time.time() - (days * 86400)
        session_files = [path for path in session_files if path.stat().st_mtime >= cutoff]
    return _latest_rate_limits(session_files, source=source)


def _iter_session_files(paths: AppPaths) -> Iterable[Path]:
    roots = [paths.codex_home / "sessions", paths.codex_home / "archived_sessions"]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.jsonl"):
            if path.is_file():
                yield path


def _parse_session_file(path: Path) -> SessionUsage | None:
    best: SessionUsage | None = None
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") != "event_msg":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("type") != "token_count":
                continue

            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            token_usage = info.get("total_token_usage")
            if not isinstance(token_usage, dict):
                continue

            candidate = SessionUsage(
                file_path=str(path),
                input_tokens=_as_int(token_usage.get("input_tokens")),
                cached_input_tokens=_as_int(token_usage.get("cached_input_tokens")),
                output_tokens=_as_int(token_usage.get("output_tokens")),
                reasoning_output_tokens=_as_int(token_usage.get("reasoning_output_tokens")),
                total_tokens=_as_int(token_usage.get("total_tokens")),
            )

            if candidate.total_tokens <= 0:
                continue
            if best is None or candidate.total_tokens > best.total_tokens:
                best = candidate

    return best


def _latest_rate_limits(session_files: list[Path], source: str | None = None) -> RateLimitSnapshot | None:
    latest_epoch: float | None = None
    latest_snapshot: RateLimitSnapshot | None = None

    for path in session_files:
        candidate = _parse_rate_limits_from_file(path, source=source)
        if candidate is None:
            continue
        observed_epoch, snapshot = candidate
        if latest_epoch is None or observed_epoch > latest_epoch:
            latest_epoch = observed_epoch
            latest_snapshot = snapshot

    return latest_snapshot


def _parse_rate_limits_from_file(path: Path, source: str | None = None) -> tuple[float, RateLimitSnapshot] | None:
    latest_epoch: float | None = None
    latest_snapshot: RateLimitSnapshot | None = None

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") != "event_msg":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("type") != "token_count":
                continue

            parsed = _parse_rate_limit_snapshot(event.get("timestamp"), payload.get("rate_limits"), source=source)
            if parsed is None:
                continue

            observed_epoch, snapshot = parsed
            if latest_epoch is None or observed_epoch > latest_epoch:
                latest_epoch = observed_epoch
                latest_snapshot = snapshot

    if latest_epoch is None or latest_snapshot is None:
        return None
    return latest_epoch, latest_snapshot


def _parse_rate_limit_snapshot(
    timestamp_value: object,
    payload: object,
    source: str | None = None,
) -> tuple[float, RateLimitSnapshot] | None:
    if not isinstance(payload, dict):
        return None

    primary = _parse_rate_limit_window(payload.get("primary"))
    secondary = _parse_rate_limit_window(payload.get("secondary"))
    if primary is None and secondary is None:
        return None

    observed_epoch = _parse_timestamp(timestamp_value)
    if observed_epoch is None:
        return None

    snapshot = RateLimitSnapshot(
        observed_at=str(timestamp_value),
        source=source,
        limit_id=_string_or_none(payload.get("limit_id")),
        plan_type=_string_or_none(payload.get("plan_type")),
        primary=primary,
        secondary=secondary,
    )
    return observed_epoch, snapshot


def _parse_rate_limit_window(payload: object) -> RateLimitWindow | None:
    if not isinstance(payload, dict):
        return None

    window_minutes = _as_int(payload.get("window_minutes"))
    if window_minutes <= 0:
        return None

    used_percent = _as_float(payload.get("used_percent"))
    used_percent = max(0.0, min(100.0, used_percent))
    resets_at = _optional_int(payload.get("resets_at"))

    return RateLimitWindow(
        used_percent=used_percent,
        remaining_percent=max(0.0, 100.0 - used_percent),
        window_minutes=window_minutes,
        resets_at=resets_at,
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _optional_int(value: object) -> int | None:
    result = _as_int(value)
    return result if result > 0 else None


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _parse_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
