"""Gap #3 — cross-process browser lock."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser_lock as BL


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(BL, "LOCK_PATH", tmp_path / "browser.lock")


def test_acquire_release_cycle(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert BL.is_busy() is False
    assert BL.acquire("ui") is True
    assert BL.is_busy() is True
    BL.release()
    assert BL.is_busy() is False


def test_same_pid_can_reacquire(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert BL.acquire("worker") is True
    assert BL.acquire("worker") is True  # same pid → allowed
    BL.release()


def test_other_pid_blocked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    # simulate another process holding the lock
    import json
    (tmp_path / "browser.lock").write_text(
        json.dumps({"owner": "worker", "pid": os.getpid() + 12345, "ts": time.time()})
    )
    assert BL.is_busy() is True
    assert BL.acquire("ui") is False


def test_stale_lock_is_ignored(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    import json
    (tmp_path / "browser.lock").write_text(
        json.dumps({"owner": "worker", "pid": 999999, "ts": time.time() - BL.TTL_SECONDS - 10})
    )
    assert BL.is_busy() is False  # stale → free
    assert BL.acquire("ui") is True
    BL.release()


def test_context_manager(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    with BL.browser_lock("ui") as ok:
        assert ok is True
        assert BL.is_busy() is True
    assert BL.is_busy() is False
