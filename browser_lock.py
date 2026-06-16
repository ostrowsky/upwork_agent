"""Cross-process browser lock — prevents worker and UI from driving the same
Edge profile at once (the cause of 'profile locked' hangs we hit repeatedly).

File-based, best-effort: a holder writes data/browser.lock with owner+pid+ts.
A lock older than TTL is treated as stale (a crashed holder won't block forever).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent / "data" / "browser.lock"
TTL_SECONDS = 600  # a holder is assumed dead after this


def status() -> dict | None:
    """Current lock info, or None if free/stale."""
    if not LOCK_PATH.exists():
        return None
    try:
        data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - data.get("ts", 0) > TTL_SECONDS:
        return None  # stale
    return data


def is_busy() -> bool:
    return status() is not None


def acquire(owner: str, ttl: int = TTL_SECONDS) -> bool:
    """Take the lock if free/stale. Returns False if someone else holds it."""
    cur = status()
    if cur is not None and cur.get("pid") != os.getpid():
        return False
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(
        json.dumps({"owner": owner, "pid": os.getpid(), "ts": time.time()}),
        encoding="utf-8",
    )
    return True


def release() -> None:
    try:
        cur = status()
        if cur is None or cur.get("pid") == os.getpid():
            LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


class browser_lock:
    """Context manager. `with browser_lock("ui") as ok:` → ok is False if busy."""

    def __init__(self, owner: str):
        self.owner = owner
        self.acquired = False

    def __enter__(self):
        self.acquired = acquire(self.owner)
        return self.acquired

    def __exit__(self, *exc):
        if self.acquired:
            release()
        return False
