from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        # Permission updates can fail on some filesystems; creation is enough.
        pass


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return {} if default is None else dict(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {} if default is None else dict(default)


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def remove_path(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink(missing_ok=True)


def copy_entry(src: Path, dst: Path) -> None:
    if src.is_dir():
        if dst.exists() or dst.is_symlink():
            remove_path(dst)
        shutil.copytree(src, dst, copy_function=shutil.copy2)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists() and not src.is_symlink():
        return False
    copy_entry(src, dst)
    return True


class ExclusiveFileLock:
    def __init__(self, lock_path: Path, timeout_seconds: float = 10.0, poll_interval: float = 0.1):
        self.lock_path = lock_path
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval
        self._fd: int | None = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self._fd, str(os.getpid()).encode("utf-8"))
                return
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for lock: {self.lock_path}")
                time.sleep(self.poll_interval)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        remove_path(self.lock_path)

    def __enter__(self) -> "ExclusiveFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()
