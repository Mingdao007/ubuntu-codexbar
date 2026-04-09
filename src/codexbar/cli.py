from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from .auth_identity import read_profile_identity
from .importers import import_legacy_account_backup
from .paths import AppPaths, DEFAULT_MANAGED_PATHS
from .profile_store import ProfileStore
from .switch_engine import SwitchEngine, is_codex_process_running
from .tui import run_tui
from .usage_stats import RateLimitSnapshot, RateLimitWindow, summarize_usage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codexbar", description="Original Codex profile switcher")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize codexbar directories")

    list_parser = sub.add_parser("list", help="List profiles")
    list_parser.add_argument("--json", action="store_true", dest="as_json", help="Output machine-readable JSON")

    sub.add_parser("current", help="Show active profile")
    sub.add_parser("whoami", help="Show on-disk profile and current root identity")

    mark_parser = sub.add_parser("mark-session", help="Deprecated compatibility stub")
    mark_parser.add_argument("name", nargs="?", default=None, help="Profile name to label this session with")
    mark_parser.add_argument("--clear", action="store_true", help="Clear the current session label")

    create_parser = sub.add_parser("create", help="Create a profile from a source root")
    create_parser.add_argument("name", help="Profile name")
    create_parser.add_argument("--from", dest="source_root", default=None, help="Source root (default: CODEX_HOME)")
    create_parser.add_argument("--description", default="", help="Profile description")
    create_parser.add_argument("--provider", default="", help="Provider label")
    create_parser.add_argument(
        "--managed",
        nargs="+",
        default=list(DEFAULT_MANAGED_PATHS),
        help="Managed relative paths copied from source",
    )

    capture_parser = sub.add_parser("capture", help="Capture the current root auth/config into a profile")
    capture_parser.add_argument("name", help="Profile name")
    capture_parser.add_argument("--from", dest="source_root", default=None, help="Source root (default: CODEX_HOME)")
    capture_parser.add_argument("--description", default=None, help="Profile description (preserve existing if omitted)")
    capture_parser.add_argument("--provider", default=None, help="Provider label (preserve existing if omitted)")
    capture_parser.add_argument("--overwrite", action="store_true", help="Overwrite an existing profile")
    capture_parser.add_argument(
        "--managed",
        nargs="+",
        default=None,
        help="Managed relative paths copied from source (default: existing managed paths or auth/config)",
    )

    activate_parser = sub.add_parser("activate", help="Activate a profile on disk for future Codex launches")
    activate_parser.add_argument("name", help="Target profile name")
    activate_parser.add_argument("--allow-running", action="store_true", help="Switch even if Codex process seems running")
    activate_parser.add_argument("--dry-run", action="store_true", help="Preview activation without modifying files")

    switch_parser = sub.add_parser("switch", help="Switch to target profile")
    switch_parser.add_argument("name", help="Target profile name")
    switch_parser.add_argument("--allow-running", action="store_true", help="Switch even if Codex process seems running")
    switch_parser.add_argument("--dry-run", action="store_true", help="Preview switch without modifying files")

    import_parser = sub.add_parser("import-legacy", help="Import legacy ~/.codex/account_backup profiles")
    import_parser.add_argument("--source", default=None, help="Source directory")
    import_parser.add_argument("--prefix", default="legacy-", help="Prefix for imported profile names")

    validate_parser = sub.add_parser("validate", help="Validate profile payload integrity")
    validate_parser.add_argument("name", nargs="?", default=None, help="Profile name; defaults to all profiles")

    doctor_parser = sub.add_parser("doctor", help="Run environment diagnostics")
    doctor_parser.add_argument("--json", action="store_true", dest="as_json", help="Output machine-readable JSON")

    usage_parser = sub.add_parser("usage", help="Summarize local token usage from session logs")
    usage_parser.add_argument("--days", type=int, default=None, help="Only include session files modified in the last N days")
    usage_parser.add_argument("--top", type=int, default=5, help="Show top N sessions by total tokens")
    usage_parser.add_argument(
        "--all",
        action="store_true",
        dest="all_profiles",
        help="Show saved session snapshots for all saved profiles",
    )
    usage_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Deprecated compatibility no-op; usage --all only reads saved session snapshots",
    )
    usage_parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="Deprecated compatibility no-op when reading saved session snapshots",
    )
    usage_parser.add_argument("--history", action="store_true", help="Also show local session totals and top sessions")
    usage_parser.add_argument("--json", action="store_true", dest="as_json", help="Output machine-readable JSON")

    tui_parser = sub.add_parser("tui", help="Interactive profile switcher")
    tui_parser.add_argument("--allow-running", action="store_true", help="Switch even if Codex process seems running")

    return parser


def cmd_init(store: ProfileStore) -> int:
    store.ensure_layout()
    print(f"Initialized: {store.paths.codexbar_home}")
    return 0


def cmd_list(store: ProfileStore, as_json: bool) -> int:
    profiles = store.list_profiles()
    state = store.state_snapshot()
    active = state["active_profile"]
    relationships = store.profile_relationships()
    if as_json:
        payload = {
            "active_profile": active,
            "profiles": [
                {
                    **profile.to_dict(),
                    **relationships.get(profile.name, {}),
                }
                for profile in profiles
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    if not profiles:
        print("No profiles found.")
        return 0

    for profile in profiles:
        tags: list[str] = []
        if profile.name == active:
            tags.append("active")
        provider = f" [{profile.provider}]" if profile.provider else ""
        identity = _format_identity(profile)
        relation = relationships.get(profile.name, {})
        suffix = f" ({', '.join(tags)})" if tags else ""
        print(f"{profile.name}{provider}{suffix}")
        if identity:
            print(f"  {identity}")
        if relation.get("duplicate_of"):
            print(f"  Duplicate of {relation['duplicate_of']}")
    return 0


def cmd_current(store: ProfileStore) -> int:
    current = store.active_profile_name()
    if current is None:
        print("No active profile.")
        return 1
    print(current)
    return 0


def cmd_whoami(store: ProfileStore) -> int:
    state = store.state_snapshot()
    identity = read_profile_identity(store.paths.codex_home / "auth.json")
    active_profile = state["active_profile"]
    relationships = store.profile_relationships()
    active_relation = relationships.get(active_profile, {}) if active_profile else {}
    canonical = store.canonical_profile_for_identity(identity)

    active_display = active_profile or "-"
    if active_relation.get("duplicate_of"):
        active_display = f"{active_display} (duplicate of {active_relation['duplicate_of']})"

    print(f"Active profile: {active_display}")
    print(f"Root identity: {_identity_summary(identity) or '-'}")
    print(f"Canonical saved profile: {canonical.name if canonical else '-'}")
    print(f"Codex process running: {is_codex_process_running()}")
    return 0


def cmd_mark_session(store: ProfileStore, args: argparse.Namespace) -> int:
    raise RuntimeError(
        "mark-session is deprecated: codexbar now treats profiles as saved login accounts, not terminal sessions"
    )


def cmd_create(store: ProfileStore, args: argparse.Namespace) -> int:
    source_root = Path(args.source_root).expanduser() if args.source_root else store.paths.codex_home
    meta = store.create_profile_from_root(
        name=args.name,
        source_root=source_root,
        managed_paths=args.managed,
        description=args.description,
        provider=args.provider,
    )
    print(f"Created profile: {meta.name}")
    print(f"Managed paths: {', '.join(meta.managed_paths)}")
    if meta.identity.summary():
        print(f"Identity: {meta.identity.summary()}")
    return 0


def cmd_capture(store: ProfileStore, args: argparse.Namespace) -> int:
    source_root = Path(args.source_root).expanduser() if args.source_root else store.paths.codex_home
    existed = store.profile_exists(args.name)
    meta = store.capture_profile_from_root(
        name=args.name,
        source_root=source_root,
        managed_paths=args.managed,
        description=args.description,
        provider=args.provider,
        overwrite=args.overwrite,
    )
    action = "Updated" if existed else "Captured"
    print(f"{action} profile: {meta.name}")
    print(f"Managed paths: {', '.join(meta.managed_paths)}")
    if meta.identity.summary():
        print(f"Identity: {meta.identity.summary()}")
    return 0


def cmd_activate(store: ProfileStore, engine: SwitchEngine, args: argparse.Namespace) -> int:
    result = engine.switch(args.name, allow_running=args.allow_running, dry_run=args.dry_run)
    if result.dry_run:
        print(f"Dry run: {result.from_profile or '-'} -> {result.to_profile}")
    else:
        print(f"Activated: {result.from_profile or '-'} -> {result.to_profile}")
    print(f"Snapshot: {result.snapshot_id}")
    print(f"Managed: {', '.join(result.changed_paths)}")
    return 0


def cmd_import_legacy(store: ProfileStore, args: argparse.Namespace) -> int:
    source_root = Path(args.source).expanduser() if args.source else store.paths.legacy_backup_root
    summary = import_legacy_account_backup(store, source_root=source_root, prefix=args.prefix)
    print(f"Imported: {len(summary.imported)}")
    for item in summary.imported:
        print(f"  + {item}")
    print(f"Skipped: {len(summary.skipped)}")
    for item in summary.skipped:
        print(f"  - {item}")
    if summary.failed:
        print(f"Failed: {len(summary.failed)}")
        for item in summary.failed:
            print(f"  ! {item}")
        return 1
    return 0


def cmd_validate(store: ProfileStore, args: argparse.Namespace) -> int:
    names = [args.name] if args.name else [profile.name for profile in store.list_profiles()]
    if not names:
        print("No profiles to validate.")
        return 0

    relationships = store.profile_relationships()
    has_error = False
    for name in names:
        errors = store.validate_profile(name)
        relation = relationships.get(name, {})
        suffix = f" (duplicate of {relation['duplicate_of']})" if relation.get("duplicate_of") else ""
        if not errors:
            print(f"{name}: OK{suffix}")
            continue
        has_error = True
        print(f"{name}: INVALID{suffix}")
        for err in errors:
            print(f"  - {err}")

    return 1 if has_error else 0


def cmd_doctor(store: ProfileStore, as_json: bool) -> int:
    store.ensure_layout()
    state = store.state_snapshot()
    root_identity = read_profile_identity(store.paths.codex_home / "auth.json")
    relationships = store.profile_relationships()
    canonical = store.canonical_profile_for_identity(root_identity)
    profiles_payload = []
    for profile in store.list_profiles():
        relation = relationships.get(profile.name, {})
        profiles_payload.append(
            {
                "name": profile.name,
                "identity": profile.identity.to_dict(),
                "duplicate_of": relation.get("duplicate_of"),
                "is_canonical": relation.get("is_canonical", True),
            }
        )
    data = {
        "codex_home": str(store.paths.codex_home),
        "codexbar_home": str(store.paths.codexbar_home),
        "codex_home_exists": store.paths.codex_home.is_dir(),
        "root_auth_exists": (store.paths.codex_home / "auth.json").is_file(),
        "profiles_count": len(store.list_profiles()),
        "active_profile": state["active_profile"],
        "root_identity": root_identity.to_dict(),
        "root_canonical_profile": canonical.name if canonical else None,
        "profiles": profiles_payload,
        "codex_process_running": is_codex_process_running(),
    }
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        for key in [
            "codex_home",
            "codexbar_home",
            "codex_home_exists",
            "root_auth_exists",
            "profiles_count",
            "active_profile",
            "root_identity",
            "root_canonical_profile",
            "codex_process_running",
        ]:
            value = data[key]
            print(f"{key}: {value}")
        print("profiles:")
        for profile in profiles_payload:
            suffix = f" duplicate of {profile['duplicate_of']}" if profile["duplicate_of"] else ""
            print(f"  - {profile['name']}{suffix}")

    return 0


def cmd_usage(paths: AppPaths, store: ProfileStore, args: argparse.Namespace) -> int:
    if args.refresh and not args.all_profiles:
        raise ValueError("--refresh requires --all")

    summary = summarize_usage(paths, days=args.days, top=args.top)
    state = store.state_snapshot()
    active_profile = state["active_profile"]
    root_identity = read_profile_identity(paths.codex_home / "auth.json")
    relationships = store.profile_relationships()
    canonical_root_profile = store.canonical_profile_for_identity(root_identity)
    current_root_live_snapshot = _normalize_current_root_live_snapshot(
        summary.current_rate_limits,
        canonical_root_profile=canonical_root_profile,
        root_identity=root_identity,
    )
    summary.current_rate_limits = current_root_live_snapshot

    if current_root_live_snapshot and canonical_root_profile is not None:
        store.write_quota_cache(canonical_root_profile.name, current_root_live_snapshot.with_source("root-session"))

    if args.all_profiles:
        canonical_root_profile_name = canonical_root_profile.name if canonical_root_profile else None
        root_profile_mismatch = bool(
            active_profile and canonical_root_profile_name and active_profile != canonical_root_profile_name
        )
        profile_rows = _collect_profile_usage_rows(
            store,
            relationships=relationships,
            active_profile=active_profile,
            current_root_profile=canonical_root_profile_name,
            current_root_live_snapshot=current_root_live_snapshot,
            show_refresh_status=args.refresh,
            timeout=args.timeout,
        )
        if args.as_json:
            payload = {
                "active_profile": active_profile,
                "canonical_root_profile": canonical_root_profile_name,
                "root_identity": root_identity.to_dict(),
                "root_profile_mismatch": root_profile_mismatch,
                "current_root_live_snapshot": current_root_live_snapshot.to_dict() if current_root_live_snapshot else None,
                "profiles": profile_rows,
            }
            if args.history:
                payload["history"] = summary.to_dict()
            print(json.dumps(payload, indent=2))
            return 0

        print("All profile usage")
        print(f"State active profile: {active_profile or '-'}")
        print(f"Current root profile: {canonical_root_profile_name or '-'}")
        if _identity_summary(root_identity):
            print(f"Current root identity: {_identity_summary(root_identity)}")
        if root_profile_mismatch:
            print("Root auth does not match the saved active profile.")
            if current_root_live_snapshot:
                print("Only the current-root usage block below reflects the current login.")
            else:
                print("No current-root live session snapshot was found for this login.")
            print("Per-profile rows below use saved session snapshots.")
        if current_root_live_snapshot:
            print(_current_root_usage_heading(canonical_root_profile, root_identity))
            if canonical_root_profile_name:
                print(f"  Profile: {canonical_root_profile_name}")
            print(f"  As of: {_format_iso_datetime(current_root_live_snapshot.observed_at)}")
            if current_root_live_snapshot.plan_type or current_root_live_snapshot.limit_id:
                plan = current_root_live_snapshot.plan_type or "-"
                limit_id = current_root_live_snapshot.limit_id or "-"
                print(f"  Plan: {plan} ({limit_id})")
            print(f"  Source: {_current_root_usage_source_detail(canonical_root_profile, root_identity)}")
            for line in _format_snapshot_windows(current_root_live_snapshot):
                print(f"  {line}")
        for row in profile_rows:
            print(row["header"])
            if row["identity"]:
                print(f"  Identity: {row['identity']}")
            if row["status"]:
                print(f"  {row['status']}")
            if row["display_snapshot"]:
                print(
                    f"  {row['display_snapshot_label']}: "
                    f"{row['display_snapshot']['observed_at']} via {row['display_snapshot']['source'] or 'unknown'}"
                )
                for line in row["windows"]:
                    print(f"  {line}")
                if row["display_snapshot_age"]:
                    print(f"  Snapshot age: {row['display_snapshot_age']}")
            elif not row["status"]:
                print("  Usage: unknown")
            if row["refresh_error"]:
                print(f"  Status probe: failed ({row['refresh_error']})")
            if row["refresh_status"]:
                print(f"  Status probe: {row['refresh_status']}")

        if args.history:
            print()
            _print_history(summary, args.days)
        return 0

    if args.as_json:
        payload = summary.to_dict()
        payload["active_profile"] = active_profile
        payload["canonical_root_profile"] = canonical_root_profile.name if canonical_root_profile else None
        print(json.dumps(payload, indent=2))
        return 0

    if summary.current_rate_limits:
        snapshot = summary.current_rate_limits
        active_relation = relationships.get(active_profile, {}) if active_profile else {}
        active_display = active_profile or "-"
        if active_relation.get("duplicate_of"):
            active_display = f"{active_display} (duplicate of {active_relation['duplicate_of']})"
        print("Remaining usage")
        print(f"Active profile: {active_display}")
        print(f"Canonical saved profile: {canonical_root_profile.name if canonical_root_profile else '-'}")
        print(f"As of: {_format_iso_datetime(snapshot.observed_at)}")
        if snapshot.plan_type or snapshot.limit_id:
            plan = snapshot.plan_type or "-"
            limit_id = snapshot.limit_id or "-"
            print(f"Plan: {plan} ({limit_id})")
        if _identity_summary(root_identity):
            print(f"Identity: {_identity_summary(root_identity)}")
        if snapshot.primary:
            print(_format_window_line(snapshot.primary))
        if snapshot.secondary:
            print(_format_window_line(snapshot.secondary))
    else:
        print("Remaining usage")
        print("No recent rate-limit data found in local session logs.")

    if not args.history:
        return 0

    print()
    _print_history(summary, args.days)

    return 0


def cmd_tui(store: ProfileStore, engine: SwitchEngine, args: argparse.Namespace) -> int:
    return run_tui(store, engine, allow_running=args.allow_running)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    paths = AppPaths.from_env()
    store = ProfileStore(paths)
    engine = SwitchEngine(paths, store)

    try:
        if args.command == "init":
            return cmd_init(store)
        if args.command == "list":
            return cmd_list(store, args.as_json)
        if args.command == "current":
            return cmd_current(store)
        if args.command == "whoami":
            return cmd_whoami(store)
        if args.command == "mark-session":
            return cmd_mark_session(store, args)
        if args.command == "create":
            return cmd_create(store, args)
        if args.command == "capture":
            return cmd_capture(store, args)
        if args.command == "activate":
            return cmd_activate(store, engine, args)
        if args.command == "switch":
            return cmd_activate(store, engine, args)
        if args.command == "import-legacy":
            return cmd_import_legacy(store, args)
        if args.command == "validate":
            return cmd_validate(store, args)
        if args.command == "doctor":
            return cmd_doctor(store, args.as_json)
        if args.command == "usage":
            return cmd_usage(paths, store, args)
        if args.command == "tui":
            return cmd_tui(store, engine, args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


def _format_window_line(window: RateLimitWindow) -> str:
    label = _format_window_label(window.window_minutes)
    if window.resets_at is None:
        return f"{label}: {window.remaining_percent:.0f}% remaining"

    seconds_left = max(0, int(window.resets_at - time.time()))
    return f"{label}: {window.remaining_percent:.0f}% remaining, resets in {_format_duration(seconds_left)} at {_format_epoch(window.resets_at)}"


def _format_window_label(window_minutes: int) -> str:
    if window_minutes % (60 * 24) == 0:
        days = window_minutes // (60 * 24)
        return f"{days // 7}w" if days % 7 == 0 and days >= 7 else f"{days}d"
    if window_minutes % 60 == 0:
        return f"{window_minutes // 60}h"
    return f"{window_minutes}m"


def _format_duration(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _format_epoch(value: int) -> str:
    return datetime.fromtimestamp(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_iso_datetime(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        return value


def _format_identity(profile: object) -> str:
    if hasattr(profile, "identity"):
        return _identity_summary(profile.identity)
    return ""


def _identity_summary(identity: object) -> str:
    if hasattr(identity, "summary"):
        return identity.summary()
    return ""


def _print_history(summary: object, days: int | None) -> None:
    window = f"last {days} days" if days and days > 0 else "all local sessions"
    print(f"Local history ({window})")
    print(f"Scanned files: {summary.scanned_files}")
    print(f"Sessions with usage: {summary.sessions_with_usage}")
    print(f"Input tokens: {summary.input_tokens}")
    print(f"Cached input tokens: {summary.cached_input_tokens}")
    print(f"Output tokens: {summary.output_tokens}")
    print(f"Reasoning output tokens: {summary.reasoning_output_tokens}")
    print(f"Total tokens: {summary.total_tokens}")

    if summary.top_sessions:
        print("Top sessions:")
        for idx, session in enumerate(summary.top_sessions, start=1):
            print(f"  {idx}. {session.total_tokens}  {session.file_path}")


def _collect_profile_usage_rows(
    store: ProfileStore,
    relationships: dict[str, dict[str, object]],
    active_profile: str | None,
    current_root_profile: str | None,
    current_root_live_snapshot: object,
    show_refresh_status: bool,
    timeout: int,
) -> list[dict[str, object]]:
    profiles = store.list_profiles()
    rows: list[dict[str, object]] = []
    for profile in profiles:
        relation = relationships.get(profile.name, {})
        duplicate_of = relation.get("duplicate_of")
        refresh_error = None
        refresh_status = None
        status = None
        snapshot = None if duplicate_of else _normalize_snapshot_for_display(store.read_quota_cache(profile.name))
        if duplicate_of:
            status = f"Duplicate identity of {duplicate_of}; usage suppressed"

        tags: list[str] = []
        if profile.name == active_profile:
            tags.append("active")
        if profile.name == current_root_profile:
            tags.append("current root")
        header = profile.name
        if tags:
            header = f"{header} ({', '.join(tags)})"

        cache_payload = None
        cache_age = None
        if snapshot is not None:
            cache_payload = snapshot.to_dict()
            cache_age = _format_snapshot_age(snapshot.observed_at)

        display_snapshot = snapshot
        display_snapshot_label = None
        if (
            profile.name == current_root_profile
            and current_root_live_snapshot is not None
            and hasattr(current_root_live_snapshot, "to_dict")
        ):
            display_snapshot = current_root_live_snapshot
            display_snapshot_label = "Current root session snapshot"
        elif snapshot is not None:
            display_snapshot_label = "Cached snapshot"

        display_snapshot_payload = None
        display_snapshot_age = None
        windows: list[str] = []
        if display_snapshot is not None and hasattr(display_snapshot, "to_dict"):
            display_snapshot_payload = display_snapshot.to_dict()
            display_snapshot_age = _format_snapshot_age(display_snapshot.observed_at)
            windows = _format_snapshot_windows(display_snapshot)

        rows.append(
            {
                "name": profile.name,
                "header": header,
                "identity": profile.identity.summary(),
                "status": status,
                "duplicate_of": duplicate_of,
                "is_active": profile.name == active_profile,
                "is_current_root": profile.name == current_root_profile,
                "cache": cache_payload,
                "cache_age": cache_age,
                "display_snapshot": display_snapshot_payload,
                "display_snapshot_age": display_snapshot_age,
                "display_snapshot_label": display_snapshot_label,
                "windows": windows,
                "refresh_error": refresh_error,
                "refresh_status": refresh_status,
            }
        )

    return rows


def _format_snapshot_age(observed_at: str) -> str | None:
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    delta = max(0, int(time.time() - observed))
    return _format_duration(delta)


def _format_snapshot_windows(snapshot: object) -> list[str]:
    windows: list[str] = []
    primary = getattr(snapshot, "primary", None)
    secondary = getattr(snapshot, "secondary", None)
    if primary:
        windows.append(_format_window_line(primary))
    if secondary:
        windows.append(_format_window_line(secondary))
    return windows


def _normalize_current_root_live_snapshot(
    snapshot: object,
    canonical_root_profile: object | None,
    root_identity: object,
) -> object:
    if snapshot is None or not hasattr(snapshot, "with_source"):
        return snapshot
    snapshot = _normalize_snapshot_for_display(snapshot)
    source = _current_root_snapshot_source(canonical_root_profile, root_identity)
    if getattr(snapshot, "source", None) == source:
        return snapshot
    return snapshot.with_source(source)


def _normalize_snapshot_for_display(snapshot: object) -> object:
    if not isinstance(snapshot, RateLimitSnapshot):
        return snapshot
    primary = snapshot.primary
    if primary is None or primary.resets_at is None or primary.resets_at > time.time():
        return snapshot
    return RateLimitSnapshot(
        observed_at=snapshot.observed_at,
        source=snapshot.source,
        limit_id=snapshot.limit_id,
        plan_type=snapshot.plan_type,
        primary=RateLimitWindow(
            used_percent=0.0,
            remaining_percent=100.0,
            window_minutes=primary.window_minutes,
            resets_at=None,
        ),
        secondary=snapshot.secondary,
    )


def _current_root_snapshot_source(canonical_root_profile: object | None, root_identity: object) -> str:
    if canonical_root_profile is not None:
        return "root-session"
    if _identity_is_empty(root_identity):
        return "orphaned-root-session"
    return "unmapped-root-session"


def _current_root_usage_heading(canonical_root_profile: object | None, root_identity: object) -> str:
    if canonical_root_profile is not None:
        return "Current root usage"
    if _identity_is_empty(root_identity):
        return "Current root usage (old terminal / orphaned root-session)"
    return "Current root usage (unmapped saved profile)"


def _current_root_usage_source_detail(canonical_root_profile: object | None, root_identity: object) -> str:
    if canonical_root_profile is not None:
        return "local session snapshot"
    if _identity_is_empty(root_identity):
        return "local session snapshot from old terminal"
    return "local session snapshot from unmapped root login"


def _identity_is_empty(identity: object) -> bool:
    return bool(hasattr(identity, "is_empty") and identity.is_empty())


if __name__ == "__main__":
    raise SystemExit(main())
