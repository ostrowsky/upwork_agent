"""Day 2 verification — upwork-browser-session worker skeleton.

Covers the spec's Verification mapping:
- worker status file write/read round-trips.
- tick() writes a heartbeat plus session fields (probe mocked, no real browser).
- a failing probe does not crash the tick (loop stays alive).
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import worker  # noqa: E402


@pytest.fixture()
def status_file(tmp_path, monkeypatch):
    path = tmp_path / "worker_status.json"
    monkeypatch.setattr(worker, "DATA_DIR", tmp_path)
    monkeypatch.setattr(worker, "STATUS_PATH", path)
    # No active task in the test DB context.
    monkeypatch.setattr(worker, "_active_task", lambda: None)
    # Don't let the tick open a real browser for the messages phase.
    monkeypatch.setenv("WORKER_IMPORT_MESSAGES", "0")
    import browser_lock
    monkeypatch.setattr(browser_lock, "LOCK_PATH", tmp_path / "browser.lock")
    return path


def test_status_round_trip(status_file):
    assert worker.read_status() == {}
    worker.write_status(state="running", session_ok=True)
    data = worker.read_status()
    assert data["state"] == "running"
    assert data["session_ok"] is True
    assert "updated_at" in data
    # File is valid JSON on disk.
    on_disk = json.loads(status_file.read_text(encoding="utf-8"))
    assert on_disk["state"] == "running"


def test_tick_writes_heartbeat(status_file, monkeypatch):
    import upwork_connect

    monkeypatch.setattr(
        upwork_connect,
        "probe_session",
        lambda *a, **k: {"ok": True, "reason": "authenticated area"},
    )
    status = worker.tick()
    assert status["session_ok"] is True
    assert status["session_reason"] == "authenticated area"
    assert status["heartbeat"]
    assert status["active_task"] is None


def test_tick_survives_probe_error(status_file, monkeypatch):
    import upwork_connect

    def boom(*a, **k):
        raise RuntimeError("browser exploded")

    monkeypatch.setattr(upwork_connect, "probe_session", boom)
    status = worker.tick()  # must not raise
    assert status["session_ok"] is False
    assert "browser exploded" in status["session_reason"]


def test_detect_anomalies():
    assert worker.detect_anomalies({"session_ok": True, "last_ingest_reason": "20 tiles"}) == []
    assert "session_down" in worker.detect_anomalies({"session_ok": False})
    assert "feed_empty" in worker.detect_anomalies({"session_ok": True, "last_ingest_reason": "0 tiles — debug"})
    assert "qualify_errors" in worker.detect_anomalies({"last_qualify_errors": 3})
    assert "draft_errors" in worker.detect_anomalies({"last_draft_errors": 1})
    assert "submit_problem" in worker.detect_anomalies({"last_submit_reason": "not confirmed (url=...)"})


def test_safe_tick_survives_crash(status_file, monkeypatch):
    # A crashing tick must not propagate — the loop keeps running.
    monkeypatch.setattr(worker, "tick", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    status = worker.safe_tick()  # must not raise
    assert status["last_error"].startswith("RuntimeError")
    assert status["state"] == "running"


def test_detect_connects_low():
    a = worker.detect_anomalies({"last_submit_reason": "insufficient connects — top up to submit"})
    assert "connects_low" in a and "submit_problem" not in a


def test_tick_count_increments(status_file, monkeypatch):
    import upwork_connect

    monkeypatch.setattr(upwork_connect, "probe_session", lambda *a, **k: {"ok": True, "reason": "auth"})
    worker.tick()
    worker.tick()
    assert worker.read_status()["tick_count"] == 2


def test_probe_throttled(status_file, monkeypatch):
    """PROBE_EVERY=2 → probe runs on ticks 1 and 3, skipped on 2 (session carried)."""
    import upwork_connect

    calls = {"n": 0}

    def probe(*a, **k):
        calls["n"] += 1
        return {"ok": True, "reason": "auth"}

    monkeypatch.setattr(upwork_connect, "probe_session", probe)
    monkeypatch.setenv("PROBE_EVERY", "2")
    worker.tick()  # 1 → probe
    skipped = worker.tick()  # 2 → skip
    worker.tick()  # 3 → probe
    assert calls["n"] == 2
    assert skipped["session_ok"] is True  # last known state carried forward
    assert "throttled" in skipped["session_reason"]


def test_msg_import_throttled(status_file, monkeypatch):
    """MSG_IMPORT_EVERY=3 → inbox import runs only on tick 1 of every 3."""
    import upwork_connect
    import messages

    monkeypatch.setattr(upwork_connect, "probe_session", lambda *a, **k: {"ok": True, "reason": "auth"})
    calls = {"n": 0}

    def imp(*a, **k):
        calls["n"] += 1
        return {"imported": 0, "reason": "ok"}

    monkeypatch.setattr(messages, "import_messages", imp)
    monkeypatch.setenv("WORKER_IMPORT_MESSAGES", "1")
    monkeypatch.setenv("MSG_IMPORT_EVERY", "3")
    for _ in range(3):
        worker.tick()
    assert calls["n"] == 1


NOW = 1_000_000.0


def test_first_anomaly_alerts():
    assert worker.should_alert({}, ["session_down"], now=NOW) is True


def test_no_anomalies_never_alerts():
    assert worker.should_alert({}, [], now=NOW) is False


def test_same_anomaly_is_not_repeated_within_cooldown(monkeypatch):
    """Regression: a flapping session sent the same session_down alert four
    times in one hour, because 'changed since last tick' alone lets a set that
    clears and returns re-alert immediately."""
    monkeypatch.setenv("ALERT_REPEAT_COOLDOWN", "3600")
    prev = {"alerted_anomalies": ["session_down"], "alerted_at": NOW - 600}
    assert worker.should_alert(prev, ["session_down"], now=NOW) is False


def test_same_anomaly_alerts_again_after_cooldown(monkeypatch):
    monkeypatch.setenv("ALERT_REPEAT_COOLDOWN", "3600")
    prev = {"alerted_anomalies": ["session_down"], "alerted_at": NOW - 3601}
    assert worker.should_alert(prev, ["session_down"], now=NOW) is True


def test_new_anomaly_alerts_immediately_even_inside_cooldown(monkeypatch):
    """A genuinely new problem must not be swallowed by the cooldown."""
    monkeypatch.setenv("ALERT_REPEAT_COOLDOWN", "3600")
    prev = {"alerted_anomalies": ["session_down"], "alerted_at": NOW - 60}
    assert worker.should_alert(prev, ["session_down", "submit_problem"], now=NOW) is True


def test_cooldown_of_zero_disables_throttling(monkeypatch):
    monkeypatch.setenv("ALERT_REPEAT_COOLDOWN", "0")
    prev = {"alerted_anomalies": ["session_down"], "alerted_at": NOW}
    assert worker.should_alert(prev, ["session_down"], now=NOW) is True
