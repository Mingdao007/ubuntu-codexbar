from __future__ import annotations

# pyright: reportMissingImports=false

import base64
import json

import pytest

from codexbar.importers import import_legacy_account_backup
from codexbar.paths import AppPaths
from codexbar.profile_store import ProfileStore
from codexbar.switch_engine import SwitchEngine


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _token(path):
    return json.loads(path.read_text(encoding="utf-8"))["token"]


def _encode_jwt(payload):
    header = {"alg": "none", "typ": "JWT"}

    def _encode(value):
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    return f"{_encode(header)}.{_encode(payload)}."


def _auth_payload(account_id, token):
    claims = {
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_plan_type": "team",
            "organizations": [
                {
                    "id": "org-1",
                    "is_default": True,
                    "role": "owner",
                    "title": "Personal",
                }
            ],
        }
    }
    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "token": token,
        "tokens": {
            "account_id": account_id,
            "id_token": _encode_jwt(claims),
            "access_token": "",
            "refresh_token": "",
        },
    }


@pytest.fixture
def app_paths(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    codex_home.mkdir(parents=True, exist_ok=True)
    return AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)


def test_create_and_switch_updates_active_and_backup(app_paths):
    store = ProfileStore(app_paths)
    store.ensure_layout()

    _write_json(app_paths.codex_home / "auth.json", {"token": "A"})
    store.create_profile_from_root("alpha", app_paths.codex_home)

    _write_json(app_paths.codex_home / "auth.json", {"token": "B"})
    store.create_profile_from_root("beta", app_paths.codex_home)
    store.set_active_profile("beta")

    engine = SwitchEngine(app_paths, store)

    result = engine.switch("alpha", allow_running=True)
    assert result.to_profile == "alpha"
    assert _token(app_paths.codex_home / "auth.json") == "A"
    assert store.active_profile_name() == "alpha"

    _write_json(app_paths.codex_home / "auth.json", {"token": "C"})
    result = engine.switch("beta", allow_running=True)
    assert result.to_profile == "beta"
    assert _token(app_paths.codex_home / "auth.json") == "B"
    assert _token(store.payload_path("alpha", "auth.json")) == "C"


def test_switch_rollback_restores_root_state(app_paths, monkeypatch):
    store = ProfileStore(app_paths)
    store.ensure_layout()

    _write_json(app_paths.codex_home / "auth.json", {"token": "A"})
    store.create_profile_from_root("alpha", app_paths.codex_home)

    _write_json(app_paths.codex_home / "auth.json", {"token": "B"})
    store.create_profile_from_root("beta", app_paths.codex_home)
    store.set_active_profile("beta")

    engine = SwitchEngine(app_paths, store)

    def _boom(target_profile, target_managed, managed_union):
        _write_json(app_paths.codex_home / "auth.json", {"token": "BROKEN"})
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(engine, "_apply_target_profile", _boom)

    with pytest.raises(RuntimeError):
        engine.switch("alpha", allow_running=True)

    assert _token(app_paths.codex_home / "auth.json") == "B"
    assert store.active_profile_name() == "beta"


def test_switch_uses_current_root_identity_when_state_is_stale(app_paths):
    store = ProfileStore(app_paths)
    store.ensure_layout()

    alpha_dir = app_paths.codex_home.parent / "alpha"
    cloud_dir = app_paths.codex_home.parent / "cloud"
    beta_dir = app_paths.codex_home.parent / "beta"
    _write_json(alpha_dir / "auth.json", _auth_payload("acct-alpha", "A"))
    _write_json(cloud_dir / "auth.json", _auth_payload("acct-cloud", "C1"))
    _write_json(beta_dir / "auth.json", _auth_payload("acct-beta", "B"))
    store.create_profile_from_directory("alpha", alpha_dir, ["auth.json"])
    store.create_profile_from_directory("云端", cloud_dir, ["auth.json"])
    store.create_profile_from_directory("beta", beta_dir, ["auth.json"])
    store.set_active_profile("alpha")

    _write_json(app_paths.codex_home / "auth.json", _auth_payload("acct-cloud", "C2"))

    engine = SwitchEngine(app_paths, store)
    result = engine.switch("beta", allow_running=True)

    assert result.from_profile == "云端"
    assert _token(app_paths.codex_home / "auth.json") == "B"
    assert _token(store.payload_path("云端", "auth.json")) == "C2"
    assert _token(store.payload_path("alpha", "auth.json")) == "A"
    assert store.active_profile_name() == "beta"


def test_import_legacy_profiles(app_paths, tmp_path):
    store = ProfileStore(app_paths)
    store.ensure_layout()

    legacy_root = tmp_path / "legacy"
    (legacy_root / "a").mkdir(parents=True)
    (legacy_root / "b").mkdir(parents=True)
    _write_json(legacy_root / "a" / "auth.json", {"token": "A"})

    summary = import_legacy_account_backup(store, legacy_root, prefix="imp-")

    assert summary.imported == ["imp-a"]
    assert any("imp-b" in item for item in summary.skipped)
    assert store.profile_exists("imp-a")
