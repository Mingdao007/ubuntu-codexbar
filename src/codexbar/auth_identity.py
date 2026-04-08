from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .models import ProfileIdentity


OPENAI_AUTH_CLAIMS_KEY = "https://api.openai.com/auth"


def read_profile_identity(auth_path: Path) -> ProfileIdentity:
    if not auth_path.is_file():
        return ProfileIdentity()

    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ProfileIdentity()

    return profile_identity_from_auth_payload(payload)


def profile_identity_from_auth_payload(payload: dict[str, Any]) -> ProfileIdentity:
    tokens = payload.get("tokens")
    tokens_dict = tokens if isinstance(tokens, dict) else {}
    claims = _decode_jwt_claims(tokens_dict.get("id_token"))
    auth_claims = claims.get(OPENAI_AUTH_CLAIMS_KEY)
    auth_info = auth_claims if isinstance(auth_claims, dict) else {}

    account_id = _as_text(tokens_dict.get("account_id")) or _as_text(auth_info.get("chatgpt_account_id"))
    plan_type = _as_text(auth_info.get("chatgpt_plan_type"))
    org_id = ""
    org_title = ""

    organizations = auth_info.get("organizations")
    if isinstance(organizations, list):
        preferred = None
        for entry in organizations:
            if not isinstance(entry, dict):
                continue
            if entry.get("is_default"):
                preferred = entry
                break
            if preferred is None:
                preferred = entry
        if isinstance(preferred, dict):
            org_id = _as_text(preferred.get("id"))
            org_title = _as_text(preferred.get("title"))

    if not any([account_id, plan_type, org_id, org_title]):
        return ProfileIdentity()

    return ProfileIdentity(
        account_id=account_id,
        plan_type=plan_type,
        org_id=org_id,
        org_title=org_title,
        identity_source="auth_json_claims",
    )


def _decode_jwt_claims(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    parts = value.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + ("=" * (-len(parts[1]) % 4))
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        data = json.loads(decoded.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text
