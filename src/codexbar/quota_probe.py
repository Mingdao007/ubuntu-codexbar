from __future__ import annotations

import errno
import os
import re
import select
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .fs_utils import copy_if_exists, ensure_private_dir
from .profile_store import ProfileStore
from .usage_stats import RateLimitSnapshot, RateLimitWindow


_STATUS_COMMAND = "/status\n"
_QUIT_COMMAND = "/quit\n"
_READ_CHUNK_SIZE = 65536
_QUIT_GRACE_SECONDS = 3.0
_MAX_PROBE_ATTEMPTS = 2
_FIRST_ATTEMPT_MAX_SECONDS = 6.0
_FIRST_ATTEMPT_MIN_SECONDS = 4.0
_STATUS_SEND_DELAYS = (0.75, 2.0)
_EMPTY_OUTPUT_GRACE_SECONDS = 4.5
_STATUS_CHUNK_LOOKAHEAD = 240
_PRIMARY_WINDOW_RE = re.compile(r"(?i)\b5\s*h(?:ours?)?\b")
_SECONDARY_WINDOW_RE = re.compile(r"(?i)\b(?:1\s*w(?:eek)?|1w)\b")
_USED_PATTERNS = (
    re.compile(r"(?i)\bused\b\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%"),
    re.compile(r"(?i)(\d+(?:\.\d+)?)\s*%\s*\bused\b"),
    re.compile(r"(?i)\busage\b\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%"),
)
_REMAINING_PATTERNS = (
    re.compile(r"(?i)\bremaining\b\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%"),
    re.compile(r"(?i)(\d+(?:\.\d+)?)\s*%\s*\bremaining\b"),
    re.compile(r"(?i)\bleft\b\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%"),
    re.compile(r"(?i)(\d+(?:\.\d+)?)\s*%\s*\bleft\b"),
    re.compile(r"(?i)\bavailable\b\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%"),
)
_GENERIC_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_PLAN_RE = re.compile(r"(?i)\bplan\b\s*[:=]?\s*([a-z0-9_-]+)")
_LIMIT_ID_RE = re.compile(r"(?i)\blimit(?:\s+id)?\b\s*[:=]?\s*([a-z0-9_-]+)")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SINGLE_ESCAPE_RE = re.compile(r"\x1b[@-_]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_PROBE_PREFIX_SANITIZE_RE = re.compile(r"[^a-z0-9]+")
_LOGIN_MARKERS = (
    "welcome to codex",
    "sign in with chatgpt",
    "sign in with device code",
    "provide your own api key",
    "usage included with plus",
    "press enter to continue",
)


class ProbeFailure(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def refresh_profile_quota(
    store: ProfileStore,
    profile_name: str,
    codex_command: str = "codex",
    timeout_seconds: int = 20,
) -> RateLimitSnapshot:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    failures: list[ProbeFailure] = []

    for attempt_index in range(_MAX_PROBE_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempt_timeout = _attempt_timeout_seconds(remaining, attempt_index)
        try:
            return _refresh_profile_quota_once(
                store,
                profile_name,
                codex_command=codex_command,
                timeout_seconds=attempt_timeout,
            )
        except ProbeFailure as exc:
            failures.append(exc)
            if not _should_retry_probe_failure(exc, attempt_index):
                break

    if failures:
        raise RuntimeError(_format_probe_failures(profile_name, failures))
    raise RuntimeError(f"Profile '{profile_name}' probe failed after retry: probe timeout after retry")


def _refresh_profile_quota_once(
    store: ProfileStore,
    profile_name: str,
    codex_command: str,
    timeout_seconds: float,
) -> RateLimitSnapshot:
    profile = store.get_profile(profile_name)
    auth_src = store.payload_path(profile_name, "auth.json")
    config_src = store.payload_path(profile_name, "config.toml")
    if not auth_src.is_file():
        raise FileNotFoundError(f"Profile '{profile_name}' is missing auth.json")

    scratch_parent = store.paths.codexbar_home / "probe-homes"
    ensure_private_dir(scratch_parent)
    with tempfile.TemporaryDirectory(prefix=_probe_home_prefix(profile.name), dir=scratch_parent) as temp_dir:
        scratch_root = Path(temp_dir)
        copy_if_exists(auth_src, scratch_root / "auth.json")
        copy_if_exists(config_src, scratch_root / "config.toml")

        env = os.environ.copy()
        env["CODEX_HOME"] = str(scratch_root)
        status_text = _capture_status_text(codex_command, env, timeout_seconds)

        if _looks_like_login_screen(status_text):
            raise ProbeFailure("not_signed_in", "not signed in for /status probing")

        snapshot = _parse_status_snapshot(status_text)
        if snapshot is None:
            if not status_text:
                raise ProbeFailure("empty_output", "empty /status output")
            message = _summarize_probe_output(status_text) or "unparseable /status output"
            raise ProbeFailure("parse_failure", f"unparseable /status output: {message}")

        return snapshot


def _capture_status_text(codex_command: str, env: dict[str, str], timeout_seconds: float) -> str:
    master_fd, slave_fd = os.openpty()
    process: subprocess.Popen[bytes] | None = None
    raw_output = bytearray()
    sanitized = ""
    probe_start = time.monotonic()
    deadline = probe_start + timeout_seconds
    status_attempts = 0
    quit_sent = False

    try:
        process = subprocess.Popen(
            [codex_command, "--no-alt-screen", "-C", "/tmp"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            text=False,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        os.set_blocking(master_fd, False)

        while time.monotonic() < deadline:
            elapsed = time.monotonic() - probe_start
            if status_attempts < len(_STATUS_SEND_DELAYS) and elapsed >= _STATUS_SEND_DELAYS[status_attempts]:
                _write_pty(master_fd, _STATUS_COMMAND)
                status_attempts += 1

            if (
                not raw_output
                and status_attempts >= len(_STATUS_SEND_DELAYS)
                and elapsed >= min(timeout_seconds, _EMPTY_OUTPUT_GRACE_SECONDS)
            ):
                raise ProbeFailure("empty_output", "empty /status output")

            timeout = max(0.0, min(0.2, deadline - time.monotonic()))
            ready, _, _ = select.select([master_fd], [], [], timeout)
            if ready:
                chunk = _read_pty(master_fd)
                if chunk:
                    raw_output.extend(chunk)
                    sanitized = _sanitize_terminal_output(raw_output.decode("utf-8", errors="replace"))
                    if _looks_like_login_screen(sanitized):
                        break
                    if not quit_sent and _parse_status_snapshot(sanitized) is not None:
                        _write_pty(master_fd, _QUIT_COMMAND)
                        quit_sent = True
                        deadline = min(deadline, time.monotonic() + _QUIT_GRACE_SECONDS)
                elif process.poll() is not None:
                    break

            if process.poll() is not None and not ready:
                break

        sanitized = _sanitize_terminal_output(raw_output.decode("utf-8", errors="replace"))
        if not sanitized:
            raise ProbeFailure("empty_output", "empty /status output")
        if process is not None and process.poll() is None and _parse_status_snapshot(sanitized) is None:
            raise ProbeFailure("timeout", "probe timeout after retry")
        return sanitized
    finally:
        if slave_fd != -1:
            os.close(slave_fd)
        try:
            os.close(master_fd)
        except OSError:
            pass
        if process is not None and process.poll() is None:
            _terminate_process(process)


def _write_pty(master_fd: int, text: str) -> None:
    try:
        os.write(master_fd, text.encode("utf-8"))
    except OSError:
        pass


def _read_pty(master_fd: int) -> bytes:
    try:
        return os.read(master_fd, _READ_CHUNK_SIZE)
    except OSError as exc:
        if exc.errno == errno.EIO:
            return b""
        raise


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait(timeout=1.0)


def _parse_status_snapshot(text: str) -> RateLimitSnapshot | None:
    normalized = _normalize_status_text(text)
    if not normalized:
        return None

    primary_chunk = _extract_window_chunk(normalized, _PRIMARY_WINDOW_RE, (_SECONDARY_WINDOW_RE,))
    secondary_chunk = _extract_window_chunk(normalized, _SECONDARY_WINDOW_RE, (_PRIMARY_WINDOW_RE,))
    if primary_chunk is None or secondary_chunk is None:
        return None

    primary = _parse_window_chunk(primary_chunk, 300)
    secondary = _parse_window_chunk(secondary_chunk, 10080)
    if primary is None or secondary is None:
        return None

    limit_id_match = _LIMIT_ID_RE.search(normalized)
    plan_match = _PLAN_RE.search(normalized)
    return RateLimitSnapshot(
        observed_at=_utc_timestamp(),
        source="live-probe",
        limit_id=limit_id_match.group(1).lower() if limit_id_match else "codex",
        plan_type=plan_match.group(1).lower() if plan_match else None,
        primary=primary,
        secondary=secondary,
    )


def _extract_window_chunk(text: str, label_re: re.Pattern[str], other_label_res: tuple[re.Pattern[str], ...]) -> str | None:
    matches = list(label_re.finditer(text))
    if not matches:
        return None

    last_match = matches[-1]
    start = last_match.start()
    end = min(len(text), last_match.end() + _STATUS_CHUNK_LOOKAHEAD)
    for other_label_re in other_label_res:
        other_match = other_label_re.search(text, last_match.end())
        if other_match is not None:
            end = min(end, other_match.start())
    return text[start:end]


def _parse_window_chunk(chunk: str, window_minutes: int) -> RateLimitWindow | None:
    used = _extract_percent(chunk, _USED_PATTERNS)
    remaining = _extract_percent(chunk, _REMAINING_PATTERNS)

    if used is None and remaining is None:
        generic_matches = [float(value) for value in _GENERIC_PERCENT_RE.findall(chunk)]
        if len(generic_matches) != 1:
            return None
        used = generic_matches[0]
        remaining = max(0.0, 100.0 - used)
    elif used is None:
        used = max(0.0, 100.0 - remaining)
    elif remaining is None:
        remaining = max(0.0, 100.0 - used)

    used = min(100.0, max(0.0, used))
    remaining = min(100.0, max(0.0, remaining))
    return RateLimitWindow(
        used_percent=used,
        remaining_percent=remaining,
        window_minutes=window_minutes,
        resets_at=None,
    )


def _extract_percent(chunk: str, patterns: tuple[re.Pattern[str], ...]) -> float | None:
    for pattern in patterns:
        match = pattern.search(chunk)
        if match is not None:
            return float(match.group(1))
    return None


def _looks_like_login_screen(text: str) -> bool:
    lowered = _normalize_status_text(text)
    return bool(lowered) and any(marker in lowered for marker in _LOGIN_MARKERS)


def _summarize_probe_output(text: str) -> str:
    normalized = _normalize_status_text(text)
    if len(normalized) <= 220:
        return normalized
    return "..." + normalized[-220:]


def _sanitize_terminal_output(text: str) -> str:
    cleaned = text.replace("\r", "\n")
    cleaned = _OSC_RE.sub(" ", cleaned)
    cleaned = _CSI_RE.sub(" ", cleaned)
    cleaned = _SINGLE_ESCAPE_RE.sub(" ", cleaned)
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _normalize_status_text(text: str) -> str:
    return _sanitize_terminal_output(text).lower()


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _probe_home_prefix(profile_name: str) -> str:
    normalized = _PROBE_PREFIX_SANITIZE_RE.sub("-", profile_name.lower()).strip("-")
    if not normalized:
        normalized = "profile"
    return f"codexbar-probe-{normalized}-"


def _attempt_timeout_seconds(remaining_seconds: float, attempt_index: int) -> float:
    if attempt_index + 1 >= _MAX_PROBE_ATTEMPTS:
        return max(1.0, remaining_seconds)
    fast_attempt = min(
        _FIRST_ATTEMPT_MAX_SECONDS,
        max(_FIRST_ATTEMPT_MIN_SECONDS, remaining_seconds / max(1, _MAX_PROBE_ATTEMPTS)),
    )
    return min(max(1.0, remaining_seconds), fast_attempt)


def _should_retry_probe_failure(exc: ProbeFailure, attempt_index: int) -> bool:
    if attempt_index + 1 >= _MAX_PROBE_ATTEMPTS:
        return False
    return exc.kind != "not_signed_in"


def _format_probe_failures(profile_name: str, failures: list[ProbeFailure]) -> str:
    final = failures[-1]
    if final.kind == "not_signed_in":
        return f"Profile '{profile_name}' is not signed in for /status probing"
    if len(failures) > 1:
        return f"Profile '{profile_name}' probe failed after retry: {final}"
    return f"Profile '{profile_name}' probe failed: {final}"
