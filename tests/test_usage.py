from __future__ import annotations

# pyright: reportMissingImports=false

import json

from codexbar.cli import _format_window_line
from codexbar.paths import AppPaths
from codexbar.usage_stats import RateLimitWindow
from codexbar.usage_stats import summarize_usage


def _write_lines(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")


def test_usage_summary_uses_max_total_per_session(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)

    s1 = codex_home / "sessions" / "2026" / "04" / "10" / "a.jsonl"
    _write_lines(
        s1,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 100,
                            "cached_input_tokens": 10,
                            "output_tokens": 20,
                            "reasoning_output_tokens": 5,
                            "total_tokens": 120,
                        }
                    },
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 300,
                            "cached_input_tokens": 50,
                            "output_tokens": 70,
                            "reasoning_output_tokens": 10,
                            "total_tokens": 370,
                        }
                    },
                },
            },
        ],
    )

    s2 = codex_home / "sessions" / "2026" / "04" / "10" / "b.jsonl"
    _write_lines(
        s2,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 90,
                            "cached_input_tokens": 0,
                            "output_tokens": 10,
                            "reasoning_output_tokens": 0,
                            "total_tokens": 100,
                        }
                    },
                },
            }
        ],
    )

    summary = summarize_usage(paths, days=None, top=5)

    assert summary.scanned_files == 2
    assert summary.sessions_with_usage == 2
    assert summary.total_tokens == 470
    assert summary.input_tokens == 390
    assert summary.cached_input_tokens == 50
    assert summary.output_tokens == 80
    assert summary.reasoning_output_tokens == 10
    assert summary.top_sessions[0].total_tokens == 370


def test_usage_summary_includes_latest_rate_limits(tmp_path):
    codex_home = tmp_path / "codex"
    codexbar_home = tmp_path / "codexbar"
    paths = AppPaths(codex_home=codex_home, codexbar_home=codexbar_home)

    session = codex_home / "sessions" / "2026" / "04" / "10" / "limits.jsonl"
    _write_lines(
        session,
        [
            {
                "timestamp": "2026-04-10T10:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "limit_id": "codex",
                        "plan_type": "team",
                        "primary": {
                            "used_percent": 13,
                            "window_minutes": 300,
                            "resets_at": 1775660793,
                        },
                        "secondary": {
                            "used_percent": 2,
                            "window_minutes": 10080,
                            "resets_at": 1776247593,
                        },
                    },
                },
            },
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
            },
        ],
    )

    summary = summarize_usage(paths, days=None, top=5)

    assert summary.current_rate_limits is not None
    assert summary.current_rate_limits.limit_id == "codex"
    assert summary.current_rate_limits.plan_type == "team"
    assert summary.current_rate_limits.primary is not None
    assert summary.current_rate_limits.primary.window_minutes == 300
    assert summary.current_rate_limits.primary.used_percent == 34
    assert summary.current_rate_limits.primary.remaining_percent == 66
    assert summary.current_rate_limits.secondary is not None
    assert summary.current_rate_limits.secondary.window_minutes == 10080
    assert summary.current_rate_limits.secondary.used_percent == 36
    assert summary.current_rate_limits.secondary.remaining_percent == 64


def test_format_window_line_omits_used_percent_without_reset():
    window = RateLimitWindow(
        used_percent=13,
        remaining_percent=87,
        window_minutes=300,
        resets_at=None,
    )

    line = _format_window_line(window)

    assert line == "5h: 87% remaining"


def test_format_window_line_omits_used_percent_with_reset(monkeypatch):
    window = RateLimitWindow(
        used_percent=13,
        remaining_percent=87,
        window_minutes=300,
        resets_at=1775675484,
    )
    monkeypatch.setattr("codexbar.cli.time.time", lambda: 1775671884)

    line = _format_window_line(window)

    assert "% used" not in line
    assert line.startswith("5h: 87% remaining, resets in 1h 0m at ")
