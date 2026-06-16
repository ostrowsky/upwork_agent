"""Browser self-heal: stale-lock removal + launch retry after cleanup."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser  # noqa: E402
import browser_cleanup  # noqa: E402


@pytest.fixture()
def _quiet_precleanup(monkeypatch):
    """No-op the pre-launch profile cleanup (avoid real PowerShell/file ops in tests)."""
    monkeypatch.setattr(browser_cleanup, "kill_profile_processes", lambda d: 0)
    monkeypatch.setattr(browser_cleanup, "remove_stale_locks", lambda d: 0)


def test_channel_process_name_default_msedge_not_chrome(monkeypatch):
    monkeypatch.delenv("UPWORK_BROWSER_CHANNEL", raising=False)
    assert browser_cleanup.channel_process_name() == "msedge"  # never kills Chrome by default
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "msedge")
    assert browser_cleanup.channel_process_name() == "msedge"
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "chrome")
    assert browser_cleanup.channel_process_name() == "chrome"
    monkeypatch.setenv("UPWORK_BROWSER_CHANNEL", "chromium")
    assert browser_cleanup.channel_process_name() == "chrome"  # bundled chromium runs as chrome.exe


def test_remove_stale_locks(tmp_path):
    (tmp_path / "SingletonLock").write_text("x")
    (tmp_path / "lockfile").write_text("x")
    (tmp_path / "keep.txt").write_text("x")
    removed = browser_cleanup.remove_stale_locks(tmp_path)
    assert removed == 2
    assert not (tmp_path / "SingletonLock").exists()
    assert (tmp_path / "keep.txt").exists()  # unrelated files untouched


def test_open_context_retries_after_heal(monkeypatch, tmp_path, _quiet_precleanup):
    calls = {"open": 0, "heal": 0}

    def fake_open(p, profile_dir, headless):
        calls["open"] += 1
        if calls["open"] == 1:
            raise RuntimeError("Timeout: profile locked")
        return "CTX"

    monkeypatch.setattr("upwork_connect.open_context", fake_open)
    monkeypatch.setattr(browser_cleanup, "cleanup_profile",
                        lambda d: calls.__setitem__("heal", calls["heal"] + 1) or {"killed": 1, "removed_locks": 0})

    ctx = browser._open_context_with_heal(object(), tmp_path, headless=True)
    assert ctx == "CTX"
    assert calls["open"] == 2  # first failed, retried after heal
    assert calls["heal"] == 1


def test_open_context_escalates_to_kill_all(monkeypatch, tmp_path, _quiet_precleanup):
    """Scoped heal fails twice → nuclear kill-all → third attempt succeeds."""
    calls = {"open": 0, "killall": 0}

    def fake_open(p, profile_dir, headless):
        calls["open"] += 1
        if calls["open"] < 3:
            raise RuntimeError("TargetClosed: profile held")
        return "CTX"

    monkeypatch.setattr("upwork_connect.open_context", fake_open)
    monkeypatch.setattr(browser_cleanup, "cleanup_profile", lambda d: {"killed": 0, "removed_locks": 0})
    monkeypatch.setattr(browser_cleanup, "kill_all_on_stuck_enabled", lambda: True)
    monkeypatch.setattr(browser_cleanup, "kill_all_browsers",
                        lambda: calls.__setitem__("killall", calls["killall"] + 1) or 3)

    ctx = browser._open_context_with_heal(object(), tmp_path, headless=True)
    assert ctx == "CTX"
    assert calls["open"] == 3 and calls["killall"] == 1


def test_open_context_reraises_first_error_if_retry_fails(monkeypatch, tmp_path, _quiet_precleanup):
    def always_fail(p, profile_dir, headless):
        raise RuntimeError("original launch error")

    monkeypatch.setattr("upwork_connect.open_context", always_fail)
    monkeypatch.setattr(browser_cleanup, "cleanup_profile", lambda d: {"killed": 0, "removed_locks": 0})
    monkeypatch.setattr(browser_cleanup, "kill_all_on_stuck_enabled", lambda: False)  # no nuclear

    try:
        browser._open_context_with_heal(object(), tmp_path, headless=True)
        assert False, "should have raised"
    except RuntimeError as e:
        assert "original launch error" in str(e)
