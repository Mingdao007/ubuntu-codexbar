from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .fs_utils import copy_if_exists
from .paths import AppPaths
from .profile_store import ProfileStore
from .usage_stats import RateLimitSnapshot, latest_rate_limits


PROBE_PROMPT = "Reply with exactly OK.\n"


def refresh_profile_quota(
    store: ProfileStore,
    profile_name: str,
    codex_command: str = "codex",
    timeout_seconds: int = 90,
) -> RateLimitSnapshot:
    profile = store.get_profile(profile_name)
    auth_src = store.payload_path(profile_name, "auth.json")
    config_src = store.payload_path(profile_name, "config.toml")
    if not auth_src.is_file():
        raise FileNotFoundError(f"Profile '{profile_name}' is missing auth.json")

    with tempfile.TemporaryDirectory(prefix=f"codexbar-probe-{profile.name}-") as temp_dir:
        scratch_root = Path(temp_dir)
        copy_if_exists(auth_src, scratch_root / "auth.json")
        copy_if_exists(config_src, scratch_root / "config.toml")

        env = os.environ.copy()
        env["CODEX_HOME"] = str(scratch_root)
        result = subprocess.run(
            [codex_command, "exec", "--json", "--skip-git-repo-check", "-C", "/tmp"],
            input=PROBE_PROMPT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
            env=env,
        )

        snapshot = latest_rate_limits(
            AppPaths(codex_home=scratch_root, codexbar_home=scratch_root / ".codexbar"),
            source="live-probe",
        )
        if snapshot is None:
            stderr_text = result.stderr.strip()
            stdout_text = result.stdout.strip()
            message = stderr_text or stdout_text or f"probe exited with code {result.returncode}"
            raise RuntimeError(f"No rate-limit data found for profile '{profile_name}': {message}")

        return snapshot
