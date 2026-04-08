from __future__ import annotations

# pyright: reportMissingImports=false

import argparse
import base64
import json
import subprocess
from pathlib import Path

import pytest

from codexbar.auth_identity import read_profile_identity
from codexbar.cli import cmd_mark_session, cmd_usage, cmd_whoami
from codexbar.fs_utils import read_json, write_json_atomic
from codexbar.paths import AppPaths
from codexbar.profile_store import ProfileStore
from codexbar.quota_probe import refresh_profile_quota
from codexbar.usage_stats import RateLimitSnapshot, RateLimitWindow


def _encode_jwt(payload):
    header = {"alg": "none", "typ": "JWT"}

    def _encode(value):
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    return f"{_encode(header)}.{_encode(payload)}."


def _auth_payload(account_id, plan_type="team", org_id="org-1", org_title="Personal"):
    claims = {
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": plan_type,
            "organizations": [
                {
                    "id": org_id,
                    "is_default": True,
                    "role": "owner",
                    "title": org_title,
                }
            ],
        }
    }
    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "last_refresh": "2026-04-08T00:00:00Z",
        "tokens": {
            "account_id": account_id,
            "id_token": _encode_jwt(claims),
            "access_token": "",
            "refresh_token": "",
        },
    }


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_lines(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")


def _write_auth(path, account_id, plan_type="team", org_id="org-1", org_title="Personal"):
    _write_json(path, _auth_payload(account_id, plan_type=plan_type, org_id=org_id, org_title=org_title))


def _write_auth_without_account(path, plan_type="team", org_id="org-1", org_title="Personal"):
    claims = {
        "https://api.openai.com/auth": {
            "chatgpt_plan_type": plan_type,
            "organizations": [
                {
                    "id": org_id,
                    "is_default": True,
                    "role": "owner",
                    "title": org_title,
                }
            ],
        }
    }
    _write_json(
        path,
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "last_refresh": "2026-04-08T00:00:00Z",
            "tokens": {
                "id_token": _encode_jwt(claims),
                "access_token": "",
                "refresh_token": "",
            },
        },
    )


def _write_config(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('model = "gpt-5.4"\n', encoding="utf-8")


def _snapshot(observed_at, used_primary, used_secondary, source="root-session"):
    return RateLimitSnapshot(
        observed_at=observed_at,
        source=source,
        limit_id="codex",
        plan_type="team",
        primary=RateLimitWindow(
            used_percent=float(used_primary),
            remaining_percent=float(100 - used_primary),
            window_minutes=300,
            resets_at=1775675484,
        ),
        secondary=RateLimitWindow(
            used_percent=float(used_secondary),
            remaining_percent=float(100 - used_secondary),
            window_minutes=10080,
            resets_at=1776171483,
        ),
    )


def _write_rate_limit_session(path, observed_at, used_primary, used_secondary, plan_type="team"):
    _write_lines(
        path,
        [
            {
                "timestamp": observed_at,
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "limit_id": "codex",
                        "plan_type": plan_type,
                        "primary": {
                            "used_percent": used_primary,
                            "window_minutes": 300,
                            "resets_at": 1775675484,
                        },
                        "secondary": {
                            "used_percent": used_secondary,
                            "window_minutes": 10080,
                            "resets_at": 1776171483,
                        },
                    },
                },
            }
        ],
    )


def _set_profile_created_at(store, name, created_at):
    meta_path = store.paths.profiles_root / name / "meta.json"
    payload = read_json(meta_path)
    payload["created_at"] = created_at
    payload["updated_at"] = created_at
    payload["last_captured_at"] = created_at
    write_json_atomic(meta_path, payload)


def _rewrite_profile_auth(store, name, payload):
    _write_json(store.payload_path(name, "auth.json"), payload)
    meta_path = store.paths.profiles_root / name / "meta.json"
    meta = read_json(meta_path)
    meta.pop("account_id", None)
    meta.pop("plan_type", None)
    meta.pop("org_id", None)
    meta.pop("org_title", None)
    meta.pop("identity_source", None)
    write_json_atomic(meta_path, meta)


def test_read_profile_identity_from_auth_json(tmp_path):
    auth_path = tmp_path / "auth.json"
    _write_auth(auth_path, "acct-123", org_id="org-xyz", org_title="Personal")

    identity = read_profile_identity(auth_path)

    assert identity.account_id == "acct-123"
    assert identity.plan_type == "team"
    assert identity.org_id == "org-xyz"
    assert identity.org_title == "Personal"
    assert identity.identity_source == "auth_json_claims"


def test_capture_overwrite_updates_identity_and_resets_quota_cache(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    _write_auth(codex_home / "auth.json", "acct-A", org_title="Main")
    _write_config(codex_home / "config.toml")
    original = store.create_profile_from_root("alpha", codex_home, description="Alpha")
    store.write_quota_cache("alpha", _snapshot("2026-04-08T10:00:00Z", 10, 20))

    _write_auth(codex_home / "auth.json", "acct-B", org_title="KKsk")
    updated = store.capture_profile_from_root("alpha", codex_home, overwrite=True)

    assert updated.created_at == original.created_at
    assert updated.account_id == "acct-B"
    assert updated.org_title == "KKsk"
    assert store.get_profile("alpha").account_id == "acct-B"
    assert store.read_quota_cache("alpha") is None


def test_state_snapshot_includes_session_label(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    _write_auth(codex_home / "auth.json", "acct-A")
    store.create_profile_from_root("alpha", codex_home)
    store.set_active_profile("alpha")
    store.set_session_label("alpha")

    state = store.state_snapshot()

    assert state["active_profile"] == "alpha"
    assert state["session_label"] == "alpha"

    store.set_session_label(None)
    assert store.session_label() is None


def test_get_profile_backfills_identity_from_payload_for_legacy_meta(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    _write_auth(codex_home / "auth.json", "acct-A", org_title="Main")
    _write_config(codex_home / "config.toml")
    store.create_profile_from_root("alpha", codex_home)

    meta_path = store.paths.profiles_root / "alpha" / "meta.json"
    legacy_meta = read_json(meta_path)
    legacy_meta.pop("account_id", None)
    legacy_meta.pop("plan_type", None)
    legacy_meta.pop("org_id", None)
    legacy_meta.pop("org_title", None)
    legacy_meta.pop("identity_source", None)
    write_json_atomic(meta_path, legacy_meta)

    profile = store.get_profile("alpha")
    reloaded = read_json(meta_path)

    assert profile.account_id == "acct-A"
    assert profile.org_title == "Main"
    assert reloaded["account_id"] == "acct-A"
    assert reloaded["org_title"] == "Main"


def test_usage_all_prints_cached_profile_usage(tmp_path, capsys):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    alpha_dir = tmp_path / "alpha"
    beta_dir = tmp_path / "beta"
    _write_auth(alpha_dir / "auth.json", "acct-A", org_title="Main")
    _write_auth(beta_dir / "auth.json", "acct-B", org_title="KKsk")
    _write_config(alpha_dir / "config.toml")
    _write_config(beta_dir / "config.toml")
    store.create_profile_from_directory("alpha", alpha_dir, ["auth.json", "config.toml"], description="alpha")
    store.create_profile_from_directory("beta", beta_dir, ["auth.json", "config.toml"], description="beta")
    store.set_active_profile("alpha")
    store.write_quota_cache("alpha", _snapshot("2026-04-08T10:00:00Z", 25, 40))
    store.write_quota_cache("beta", _snapshot("2026-04-08T11:00:00Z", 5, 15, source="live-probe"))

    args = argparse.Namespace(
        days=None,
        top=5,
        all_profiles=True,
        refresh=False,
        timeout=30,
        history=False,
        as_json=False,
    )
    cmd_usage(paths, store, args)
    output = capsys.readouterr().out

    assert "All profile usage" in output
    assert "alpha (active)" in output
    assert "beta" in output
    assert "Identity: team / Main / acct-A" in output
    assert "Identity: team / KKsk / acct-B" in output
    assert "Cached snapshot:" in output
    assert "5h:" in output
    assert "1w:" in output
    assert "% used" not in output


def test_usage_all_reports_current_root_profile_when_state_is_stale(tmp_path, capsys):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    business_dir = tmp_path / "business"
    cloud_dir = tmp_path / "cloud"
    _write_auth(business_dir / "auth.json", "acct-business", org_title="Personal")
    _write_auth(cloud_dir / "auth.json", "acct-cloud", org_title="Personal")
    _write_config(business_dir / "config.toml")
    _write_config(cloud_dir / "config.toml")
    store.create_profile_from_directory("GPT Business", business_dir, ["auth.json", "config.toml"], description="biz")
    store.create_profile_from_directory("云端", cloud_dir, ["auth.json", "config.toml"], description="cloud")
    store.set_active_profile("GPT Business")
    _write_auth(codex_home / "auth.json", "acct-cloud", org_title="Personal")
    store.write_quota_cache("GPT Business", _snapshot("2026-04-08T10:00:00Z", 25, 40))
    store.write_quota_cache("云端", _snapshot("2026-04-08T11:00:00Z", 5, 15))

    args = argparse.Namespace(
        days=None,
        top=5,
        all_profiles=True,
        refresh=False,
        timeout=30,
        history=False,
        as_json=False,
    )
    cmd_usage(paths, store, args)
    output = capsys.readouterr().out

    assert "State active profile: GPT Business" in output
    assert "Current root profile: 云端" in output
    assert "Current root identity: team / Personal / acct-cloud" in output
    assert "Root auth does not match the saved active profile." in output
    assert "No current-root live session snapshot was found for this login." in output
    assert "Per-profile rows below are cached snapshots unless refreshed." in output
    assert "GPT Business (active)" in output
    assert "云端 (current root)" in output
    assert "Current root usage" not in output
    assert "Cached snapshot:" in output
    assert "% used" not in output


def test_usage_all_prefers_current_root_live_snapshot_when_state_is_stale(tmp_path, capsys):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    business_dir = tmp_path / "business"
    kksk_dir = tmp_path / "kksk"
    _write_auth(business_dir / "auth.json", "acct-business", org_title="Personal")
    _write_auth(kksk_dir / "auth.json", "acct-kksk", org_title="Personal")
    _write_config(business_dir / "config.toml")
    _write_config(kksk_dir / "config.toml")
    store.create_profile_from_directory("GPT Business", business_dir, ["auth.json", "config.toml"], description="biz")
    store.create_profile_from_directory("KKsk", kksk_dir, ["auth.json", "config.toml"], description="kksk")
    store.set_active_profile("GPT Business")
    _write_auth(codex_home / "auth.json", "acct-kksk", org_title="Personal")
    store.write_quota_cache("GPT Business", _snapshot("2026-04-08T10:00:00Z", 25, 40))
    store.write_quota_cache("KKsk", _snapshot("2026-04-08T11:00:00Z", 5, 15))
    _write_rate_limit_session(
        codex_home / "sessions" / "2026" / "04" / "10" / "limits.jsonl",
        "2026-04-10T10:05:00Z",
        34,
        56,
    )

    args = argparse.Namespace(
        days=None,
        top=5,
        all_profiles=True,
        refresh=False,
        timeout=30,
        history=False,
        as_json=False,
    )
    cmd_usage(paths, store, args)
    output = capsys.readouterr().out

    assert "Current root usage" in output
    assert "  Profile: KKsk" in output
    assert "  Source: local session snapshot" in output
    assert "Current root live snapshot: 2026-04-10T10:05:00Z via root-session" in output
    assert "Cached snapshot: 2026-04-08T10:00:00Z via root-session" in output
    assert "Only the current-root usage block below reflects the current login." in output
    assert "5h: 66% remaining" in output
    assert "1w: 44% remaining" in output


def test_usage_all_json_includes_current_root_live_snapshot(tmp_path, capsys):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    alpha_dir = tmp_path / "alpha"
    _write_auth(alpha_dir / "auth.json", "acct-alpha", org_title="Main")
    _write_config(alpha_dir / "config.toml")
    store.create_profile_from_directory("alpha", alpha_dir, ["auth.json", "config.toml"], description="alpha")
    store.set_active_profile("alpha")
    _write_auth(codex_home / "auth.json", "acct-alpha", org_title="Main")
    _write_rate_limit_session(
        codex_home / "sessions" / "2026" / "04" / "10" / "limits.jsonl",
        "2026-04-10T10:05:00Z",
        20,
        30,
    )

    args = argparse.Namespace(
        days=None,
        top=5,
        all_profiles=True,
        refresh=False,
        timeout=30,
        history=False,
        as_json=True,
    )
    cmd_usage(paths, store, args)
    payload = json.loads(capsys.readouterr().out)

    assert payload["canonical_root_profile"] == "alpha"
    assert payload["current_root_live_snapshot"]["observed_at"] == "2026-04-10T10:05:00Z"
    assert payload["profiles"][0]["display_snapshot_label"] == "Current root live snapshot"


def test_profile_relationships_pick_earliest_created_profile_as_canonical(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    alias_a = tmp_path / "alias-a"
    alias_b = tmp_path / "alias-b"
    _write_auth(alias_a / "auth.json", "acct-A", org_title="Main")
    _write_auth(alias_b / "auth.json", "acct-B", org_title="Other")
    _write_config(alias_a / "config.toml")
    _write_config(alias_b / "config.toml")
    store.create_profile_from_directory("main", alias_a, ["auth.json", "config.toml"], description="canonical")
    store.create_profile_from_directory("KKsk", alias_b, ["auth.json", "config.toml"], description="stale")
    _set_profile_created_at(store, "main", "2026-04-07T17:22:14Z")
    _set_profile_created_at(store, "KKsk", "2026-04-08T15:02:53Z")
    _rewrite_profile_auth(store, "KKsk", _auth_payload("acct-A", org_title="Main"))

    relationships = store.profile_relationships()

    assert relationships["main"]["is_canonical"] is True
    assert relationships["main"]["duplicate_of"] is None
    assert relationships["KKsk"]["duplicate_of"] == "main"


def test_profile_relationships_fallback_without_account_id(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    alpha_dir = tmp_path / "alpha"
    beta_dir = tmp_path / "beta"
    _write_auth_without_account(alpha_dir / "auth.json", org_id="org-shared", org_title="Shared")
    _write_auth(beta_dir / "auth.json", "acct-B", org_title="Other")
    _write_config(alpha_dir / "config.toml")
    _write_config(beta_dir / "config.toml")
    store.create_profile_from_directory("alpha", alpha_dir, ["auth.json", "config.toml"], description="alpha")
    store.create_profile_from_directory("beta", beta_dir, ["auth.json", "config.toml"], description="beta")
    _set_profile_created_at(store, "alpha", "2026-04-01T00:00:00Z")
    _set_profile_created_at(store, "beta", "2026-04-02T00:00:00Z")
    _rewrite_profile_auth(
        store,
        "beta",
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "last_refresh": "2026-04-08T00:00:00Z",
            "tokens": {
                "id_token": _encode_jwt(
                    {
                        "https://api.openai.com/auth": {
                            "chatgpt_plan_type": "team",
                            "organizations": [
                                {
                                    "id": "org-shared",
                                    "is_default": True,
                                    "role": "owner",
                                    "title": "Shared",
                                }
                            ],
                        }
                    }
                ),
                "access_token": "",
                "refresh_token": "",
            },
        },
    )

    relationships = store.profile_relationships()

    assert relationships["alpha"]["is_canonical"] is True
    assert relationships["beta"]["duplicate_of"] == "alpha"


def test_usage_all_writes_root_session_cache_to_canonical_and_suppresses_duplicate(tmp_path, capsys):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    main_dir = tmp_path / "main"
    kksk_dir = tmp_path / "kksk"
    _write_auth(main_dir / "auth.json", "acct-A", org_title="Main")
    _write_auth(kksk_dir / "auth.json", "acct-B", org_title="Other")
    _write_config(main_dir / "config.toml")
    _write_config(kksk_dir / "config.toml")
    store.create_profile_from_directory("main", main_dir, ["auth.json", "config.toml"], description="main")
    store.create_profile_from_directory("KKsk", kksk_dir, ["auth.json", "config.toml"], description="kksk")
    _set_profile_created_at(store, "main", "2026-04-07T17:22:14Z")
    _set_profile_created_at(store, "KKsk", "2026-04-08T15:02:53Z")
    _rewrite_profile_auth(store, "KKsk", _auth_payload("acct-A", org_title="Main"))
    store.set_active_profile("KKsk")
    store.write_quota_cache("KKsk", _snapshot("2026-04-08T09:00:00Z", 99, 99, source="legacy"))

    session = codex_home / "sessions" / "2026" / "04" / "10" / "limits.jsonl"
    _write_auth(codex_home / "auth.json", "acct-A", org_title="Main")
    _write_lines(
        session,
        [
            {
                "timestamp": "2026-04-10T10:05:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "limit_id": "codex",
                        "plan_type": "team",
                        "primary": {
                            "used_percent": 34,
                            "window_minutes": 300,
                            "resets_at": 1775675484,
                        },
                        "secondary": {
                            "used_percent": 36,
                            "window_minutes": 10080,
                            "resets_at": 1776171483,
                        },
                    },
                },
            }
        ],
    )

    args = argparse.Namespace(
        days=None,
        top=5,
        all_profiles=True,
        refresh=False,
        timeout=30,
        history=False,
        as_json=False,
    )
    cmd_usage(paths, store, args)
    output = capsys.readouterr().out

    assert store.read_quota_cache("main") is not None
    assert "Current root usage" in output
    assert "KKsk (active)" in output
    assert "Duplicate identity of main; usage suppressed" in output
    assert "main" in output
    assert "Current root live snapshot:" in output


def test_usage_all_refresh_skips_duplicate_profiles(tmp_path, capsys, monkeypatch):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    main_dir = tmp_path / "main"
    kksk_dir = tmp_path / "kksk"
    beta_dir = tmp_path / "beta"
    _write_auth(main_dir / "auth.json", "acct-A", org_title="Main")
    _write_auth(kksk_dir / "auth.json", "acct-B", org_title="Other")
    _write_auth(beta_dir / "auth.json", "acct-C", org_title="Beta")
    _write_config(main_dir / "config.toml")
    _write_config(kksk_dir / "config.toml")
    _write_config(beta_dir / "config.toml")
    store.create_profile_from_directory("main", main_dir, ["auth.json", "config.toml"], description="main")
    store.create_profile_from_directory("KKsk", kksk_dir, ["auth.json", "config.toml"], description="kksk")
    store.create_profile_from_directory("beta", beta_dir, ["auth.json", "config.toml"], description="beta")
    _set_profile_created_at(store, "main", "2026-04-07T17:22:14Z")
    _set_profile_created_at(store, "KKsk", "2026-04-08T15:02:53Z")
    _rewrite_profile_auth(store, "KKsk", _auth_payload("acct-A", org_title="Main"))

    calls = []

    def _fake_refresh(store_arg, profile_name, timeout_seconds=90, codex_command="codex"):
        calls.append(profile_name)
        return _snapshot("2026-04-08T11:00:00Z", 12, 34, source="live-probe")

    monkeypatch.setattr("codexbar.cli.refresh_profile_quota", _fake_refresh)

    args = argparse.Namespace(
        days=None,
        top=5,
        all_profiles=True,
        refresh=True,
        timeout=30,
        history=False,
        as_json=False,
    )
    cmd_usage(paths, store, args)
    output = capsys.readouterr().out

    assert calls == ["beta", "main"]
    assert "Refresh: skipped (duplicate identity of main)" in output


def test_mark_session_is_deprecated(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    with pytest.raises(RuntimeError, match="deprecated"):
        cmd_mark_session(store, argparse.Namespace(name="main", clear=False))


def test_create_rejects_duplicate_identity(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    _write_auth(codex_home / "auth.json", "acct-A", org_title="Main")
    _write_config(codex_home / "config.toml")
    store.create_profile_from_root("main", codex_home)

    with pytest.raises(ValueError, match="main"):
        store.create_profile_from_root("alias", codex_home)


def test_capture_overwrite_rejects_duplicate_identity_of_other_profile(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    main_dir = tmp_path / "main"
    kksk_dir = tmp_path / "kksk"
    _write_auth(main_dir / "auth.json", "acct-A", org_title="Main")
    _write_auth(kksk_dir / "auth.json", "acct-B", org_title="Other")
    _write_config(main_dir / "config.toml")
    _write_config(kksk_dir / "config.toml")
    store.create_profile_from_directory("main", main_dir, ["auth.json", "config.toml"], description="main")
    store.create_profile_from_directory("KKsk", kksk_dir, ["auth.json", "config.toml"], description="kksk")
    _set_profile_created_at(store, "main", "2026-04-07T17:22:14Z")
    _set_profile_created_at(store, "KKsk", "2026-04-08T15:02:53Z")
    _rewrite_profile_auth(store, "KKsk", _auth_payload("acct-A", org_title="Main"))
    _write_auth(codex_home / "auth.json", "acct-A", org_title="Main")

    with pytest.raises(ValueError, match="main"):
        store.capture_profile_from_root("KKsk", codex_home, overwrite=True)


def test_capture_overwrite_allows_same_identity_for_canonical_profile(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    main_dir = tmp_path / "main"
    kksk_dir = tmp_path / "kksk"
    _write_auth(main_dir / "auth.json", "acct-A", org_title="Main")
    _write_auth(kksk_dir / "auth.json", "acct-B", org_title="Other")
    _write_config(main_dir / "config.toml")
    _write_config(kksk_dir / "config.toml")
    store.create_profile_from_directory("main", main_dir, ["auth.json", "config.toml"], description="main")
    store.create_profile_from_directory("KKsk", kksk_dir, ["auth.json", "config.toml"], description="kksk")
    _set_profile_created_at(store, "main", "2026-04-07T17:22:14Z")
    _set_profile_created_at(store, "KKsk", "2026-04-08T15:02:53Z")
    _rewrite_profile_auth(store, "KKsk", _auth_payload("acct-A", org_title="Main"))
    _write_auth(codex_home / "auth.json", "acct-A", org_title="Main")

    updated = store.capture_profile_from_root("main", codex_home, overwrite=True)

    assert updated.account_id == "acct-A"
    assert store.get_profile("main").account_id == "acct-A"


def test_whoami_reports_canonical_saved_profile_for_duplicate_active_profile(tmp_path, capsys):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    main_dir = tmp_path / "main"
    kksk_dir = tmp_path / "kksk"
    _write_auth(main_dir / "auth.json", "acct-A", org_title="Main")
    _write_auth(kksk_dir / "auth.json", "acct-B", org_title="Other")
    _write_config(main_dir / "config.toml")
    _write_config(kksk_dir / "config.toml")
    store.create_profile_from_directory("main", main_dir, ["auth.json", "config.toml"], description="main")
    store.create_profile_from_directory("KKsk", kksk_dir, ["auth.json", "config.toml"], description="kksk")
    _set_profile_created_at(store, "main", "2026-04-07T17:22:14Z")
    _set_profile_created_at(store, "KKsk", "2026-04-08T15:02:53Z")
    _rewrite_profile_auth(store, "KKsk", _auth_payload("acct-A", org_title="Main"))
    store.set_active_profile("KKsk")
    _write_auth(codex_home / "auth.json", "acct-A", org_title="Main")

    cmd_whoami(store)
    output = capsys.readouterr().out

    assert "Active profile: KKsk (duplicate of main)" in output
    assert "Canonical saved profile: main" in output


def test_refresh_profile_quota_reads_scratch_sessions(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)
    store = ProfileStore(paths)
    store.ensure_layout()

    _write_auth(codex_home / "auth.json", "acct-A", org_title="Main")
    _write_config(codex_home / "config.toml")
    store.create_profile_from_root("alpha", codex_home)

    def _fake_run(args, input, stdout, stderr, text, check, timeout, env):
        session_path = Path(env["CODEX_HOME"]) / "sessions" / "2026" / "04" / "08" / "probe.jsonl"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            json.dumps(
                {
                    "timestamp": "2026-04-08T15:22:16.206Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": None,
                        "rate_limits": {
                            "limit_id": "codex",
                            "plan_type": "team",
                            "primary": {"used_percent": 2.0, "window_minutes": 300, "resets_at": 1775679643},
                            "secondary": {"used_percent": 80.0, "window_minutes": 10080, "resets_at": 1775831408},
                        },
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("codexbar.quota_probe.subprocess.run", _fake_run)

    snapshot = refresh_profile_quota(store, "alpha", timeout_seconds=5)

    assert snapshot.source == "live-probe"
    assert snapshot.primary is not None
    assert snapshot.primary.used_percent == 2.0
    assert snapshot.secondary is not None
    assert snapshot.secondary.used_percent == 80.0
