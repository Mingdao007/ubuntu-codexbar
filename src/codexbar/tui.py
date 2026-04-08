from __future__ import annotations

from .profile_store import ProfileStore
from .switch_engine import SwitchEngine


def run_tui(store: ProfileStore, engine: SwitchEngine, allow_running: bool = False) -> int:
    profiles = store.list_profiles()
    if not profiles:
        print("No profiles found. Use: codexbar create <name>")
        return 1

    current = store.active_profile_name()

    if len(profiles) == 1 and current == profiles[0].name:
        print(f"Only one profile '{current}' and it is already active.")
        print("Use 'codexbar usage' to view local token usage.")
        return 0

    print("codexbar interactive switch")
    print("---------------------------")
    for idx, profile in enumerate(profiles, start=1):
        marker = "*" if current == profile.name else " "
        provider = f" [{profile.provider}]" if profile.provider else ""
        print(f"{idx:>2}. {marker} {profile.name}{provider}")

    choice = input("Select profile number (or q to quit): ").strip().lower()
    if choice in {"q", "quit", "exit"}:
        return 0

    if not choice.isdigit():
        print("Invalid choice.")
        return 1

    selected_index = int(choice) - 1
    if selected_index < 0 or selected_index >= len(profiles):
        print("Choice out of range.")
        return 1

    target = profiles[selected_index].name
    confirm = input(f"Switch to '{target}'? [y/N]: ").strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled.")
        return 0

    result = engine.switch(target, allow_running=allow_running)
    print(f"Switched: {result.from_profile or '-'} -> {result.to_profile}")
    print(f"Snapshot: {result.snapshot_id}")
    return 0
