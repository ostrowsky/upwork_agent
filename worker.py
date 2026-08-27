"""Background worker — runs as a separate process from Streamlit.

Per AGENTS.md, the long-running automation loop must NOT live inside Streamlit.
This process owns the periodic work and writes a heartbeat/status file that the
UI reads (read-only) to show worker health.

Day 2 scope (skeleton): probe the Upwork session each tick and write a heartbeat.
Job ingestion (Day 3) and qualification (Day 4) plug into ``tick()`` later.

Usage:
  python worker.py            # run the loop until Ctrl+C
  python worker.py --once     # single tick (tests / smoke checks)
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATUS_PATH = DATA_DIR / "worker_status.json"
LOG_PATH = DATA_DIR / "worker.log"
DEFAULT_INTERVAL = int(os.getenv("WORKER_INTERVAL", "300"))

_running = True
log = logging.getLogger("worker")


def setup_logging() -> None:
    """Log every action to data/worker.log (rotating) AND stdout."""
    # Background stdout defaults to the OS console codepage (cp1251 on RU Windows),
    # which crashes on non-ASCII in print()/log — force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ts() -> float:
    """Epoch seconds — for cooldown arithmetic that survives a worker restart."""
    return time.time()


def _utc_today() -> str:
    """Today's UTC date — the daily report's once-per-day key."""
    return datetime.now(timezone.utc).date().isoformat()


def should_alert(prev: dict, anomalies: list[str], now: float | None = None) -> bool:
    """Whether this tick's anomalies warrant pushing an alert.

    "Changed since the last tick" alone is not enough: a flapping session
    (down -> up -> down) or a worker restart makes the set differ again and
    again, which is how the same session_down alert went out four times in an
    hour. A NEW set alerts immediately; an unchanged one waits out
    ALERT_REPEAT_COOLDOWN.
    """
    if not anomalies:
        return False
    if anomalies != prev.get("alerted_anomalies"):
        return True
    age = (now if now is not None else _now_ts()) - float(prev.get("alerted_at") or 0)
    return age >= _int_env("ALERT_REPEAT_COOLDOWN", 3600, minimum=0)


def report_day_for(now: datetime) -> str:
    """Which day the daily report should describe.

    The report fires on the first tick of a new UTC day, so the day worth
    reporting is the one that just ended. Labelling it with the current day
    reported a day that was minutes old, which always looked like "nothing
    happened".
    """
    return (now.date() - timedelta(days=1)).isoformat()


def read_status() -> dict:
    """Read the worker status file. Returns {} if missing or unreadable."""
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_status(**fields) -> dict:
    """Merge ``fields`` into the status file and stamp ``updated_at``."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    status = read_status()
    status.update(fields)
    status["updated_at"] = _now()
    STATUS_PATH.write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return status


def detect_anomalies(status: dict) -> list[str]:
    """Flag tick outcomes worth alerting on (wired to reporting on Day 10)."""
    a = []
    if status.get("session_ok") is False:
        a.append("session_down")
    reason = status.get("last_ingest_reason")
    if isinstance(reason, str) and re.search(r"\b0 tiles\b", reason):
        a.append("feed_empty")
    if (status.get("last_qualify_errors") or 0) > 0:
        a.append("qualify_errors")
    if (status.get("last_draft_errors") or 0) > 0:
        a.append("draft_errors")
    sr = (status.get("last_submit_reason") or "").lower()
    if "insufficient connects" in sr or "boost required" in sr:
        a.append("connects_low")  # agent should tell the operator to top up / skip boost jobs
    elif any(k in sr for k in ("error", "not found", "not confirmed")):
        a.append("submit_problem")
    return a


def _active_task():
    """The single active Task row, or None. Isolated so tests can patch it."""
    try:
        from database import get_db_session, Task
    except Exception:  # noqa: BLE001 — DB optional for a bare heartbeat
        return None
    db = get_db_session()
    try:
        return db.query(Task).filter(Task.is_active == 1).first()
    finally:
        db.close()


INGEST_LIMIT = int(os.getenv("INGEST_LIMIT", "20"))
QUALIFY_LIMIT = int(os.getenv("QUALIFY_LIMIT", "20"))
PROPOSAL_LIMIT = int(os.getenv("PROPOSAL_LIMIT", "10"))


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    """Read an int env var at call time (so tests can monkeypatch.setenv)."""
    try:
        return max(int(os.getenv(name, str(default)) or default), minimum)
    except (TypeError, ValueError):
        return default


def tick() -> dict:
    """One worker iteration: probe session, ingest the feed, write heartbeat.

    Throttling (cut idle-tick cost): PROBE_EVERY / MSG_IMPORT_EVERY run those
    browser phases only every Nth tick; SEARCH_EVERY + WORKER_SEARCH_QUERIES add
    keyword searches periodically. Defaults keep every-tick behaviour.
    """
    task = _active_task()
    task_id = task.id if task else None
    label = f"#{task.id} — {task.name}" if task else None

    # Tick counter (persisted) drives the every-Nth-tick throttles below.
    prev = read_status()
    tick_no = int(prev.get("tick_count", 0) or 0) + 1
    probe_every = _int_env("PROBE_EVERY", 1, minimum=1)
    msg_every = _int_env("MSG_IMPORT_EVERY", 1, minimum=1)
    search_every = _int_env("SEARCH_EVERY", 0, minimum=0)
    outcome_every = _int_env("OUTCOME_SYNC_EVERY", 0, minimum=0)  # 0 = off (enable in .env)
    do_probe = (tick_no - 1) % probe_every == 0
    do_msg = (tick_no - 1) % msg_every == 0
    do_search = search_every > 0 and (tick_no - 1) % search_every == 0
    do_outcomes = outcome_every > 0 and (tick_no - 1) % outcome_every == 0

    # Browser phase (probe + ingest) holds the cross-process lock so a manual
    # UI browser action doesn't fight the worker over the Edge profile.
    from browser_lock import acquire as _lock_acquire, release as _lock_release

    log.info("tick start | #%s task=%s lock=%s", tick_no, label,
             have_lock := _lock_acquire("worker"))
    probe = {"ok": False, "reason": "browser busy (ui holds lock)"}
    ingest = {"added": 0, "skipped_dup": 0, "reason": "skipped"}
    search = {"added": 0, "reason": "skipped"}
    msg = {"imported": 0, "reason": "skipped"}
    if have_lock:
        if do_probe:
            try:
                from upwork_connect import probe_session

                probe = probe_session()
            except Exception as e:  # noqa: BLE001 — never let a tick kill the loop
                probe = {"ok": False, "reason": f"probe error: {e}"}
            log.info("probe | ok=%s reason=%s", probe.get("ok"), probe.get("reason"))
        else:
            # Reuse the last known session state; a stale session self-corrects on
            # the next probe tick (ingest below fails gracefully meanwhile).
            probe = {"ok": bool(prev.get("session_ok")),
                     "reason": f"probe skipped (throttled, PROBE_EVERY={probe_every})"}
            log.info("probe | skipped (throttled) assuming ok=%s", probe["ok"])

        # Ingest only when authenticated and a task is active to attribute jobs to.
        if probe.get("ok") and task_id is not None:
            try:
                from jobs import ingest_from_upwork

                ingest = ingest_from_upwork(task_id, limit=INGEST_LIMIT)
            except Exception as e:  # noqa: BLE001
                ingest = {"added": 0, "skipped_dup": 0, "reason": f"ingest error: {e}"}
            log.info("ingest | added=%s skipped=%s reason=%s",
                     ingest.get("added"), ingest.get("skipped_dup"), ingest.get("reason"))

            # Keyword searches widen the funnel beyond the static best-matches feed.
            if do_search:
                queries = [q.strip() for q in os.getenv("WORKER_SEARCH_QUERIES", "").split(",") if q.strip()]
                if queries:
                    added = 0
                    parts = []
                    for q in queries:
                        try:
                            from jobs import ingest_search

                            r = ingest_search(q, task_id, limit=_int_env("SEARCH_LIMIT", 10, minimum=1))
                            added += r.get("added", 0)
                            parts.append(f"'{q}':{r.get('added', 0)}")
                        except Exception as e:  # noqa: BLE001
                            parts.append(f"'{q}':err {e}")
                    search = {"added": added, "reason": " ".join(parts)}
                    log.info("search | added=%s reason=%s", added, search["reason"])

    # Qualify freshly-ingested NEW jobs (LLM; no browser needed).
    qualify = {"applied": 0, "skipped": 0, "errors": 0, "reason": "skipped"}
    if task_id is not None:
        try:
            from qualify import qualify_new_jobs

            qualify = qualify_new_jobs(task_id, limit=QUALIFY_LIMIT)
            qualify["reason"] = f"{qualify['total']} qualified"
        except Exception as e:  # noqa: BLE001
            qualify = {"applied": 0, "skipped": 0, "errors": 0, "reason": f"qualify error: {e}"}
        log.info("qualify | applied=%s skipped=%s errors=%s",
                 qualify.get("applied"), qualify.get("skipped"), qualify.get("errors"))

    # Draft proposals for READY_TO_PROPOSE jobs (LLM; no browser).
    draft = {"drafted": 0, "errors": 0, "reason": "skipped"}
    if task_id is not None:
        try:
            from proposals import generate_drafts_for_ready

            draft = generate_drafts_for_ready(task_id, limit=PROPOSAL_LIMIT)
            draft["reason"] = f"{draft['total']} considered"
        except Exception as e:  # noqa: BLE001
            draft = {"drafted": 0, "errors": 0, "reason": f"draft error: {e}"}
        log.info("draft | drafted=%s errors=%s", draft.get("drafted"), draft.get("errors"))

    # Auto-submit drafts — ONLY when AUTO_SUBMIT=1 (irreversible, spends connects).
    submit = {"submitted": 0, "reason": "disabled"}
    if have_lock and task_id is not None and probe.get("ok"):
        try:
            from submit import submit_ready, auto_submit_enabled

            if auto_submit_enabled():
                submit = submit_ready(task_id)
                submit["reason"] = (
                    f"{submit['submitted']}/{submit['total']} sent · {submit.get('last_reason', '')[:60]}"
                )
            else:
                submit = {"submitted": 0, "reason": "AUTO_SUBMIT=0 (disabled)"}
        except Exception as e:  # noqa: BLE001
            submit = {"submitted": 0, "reason": f"submit error: {e}"}
        log.info("submit | submitted=%s reason=%s", submit.get("submitted"), submit.get("reason"))

    # Import client conversations (drafts AI replies; sends only if MSG_AUTO_SEND=1).
    msg_enabled = os.getenv("WORKER_IMPORT_MESSAGES", "1") == "1"
    if have_lock and probe.get("ok") and msg_enabled and do_msg:
        try:
            from messages import import_messages

            msg = import_messages(max_rooms=int(os.getenv("MSG_IMPORT_LIMIT", "10")))
        except Exception as e:  # noqa: BLE001
            msg = {"imported": 0, "reason": f"messages error: {e}"}
        log.info("messages | imported=%s reason=%s", msg.get("imported"), msg.get("reason"))
    elif msg_enabled and not do_msg:
        msg = {"imported": 0, "reason": f"skipped (throttled, MSG_IMPORT_EVERY={msg_every})"}
        log.info("messages | %s", msg["reason"])

    # Auto-track proposal outcomes (won/declined/closed) from the archived page.
    outcomes = {"reason": "skipped"}
    if have_lock and probe.get("ok") and do_outcomes:
        try:
            from proposals_sync import sync_outcomes_from_upwork

            outcomes = sync_outcomes_from_upwork(db=None)
        except Exception as e:  # noqa: BLE001
            outcomes = {"ok": False, "reason": f"outcomes error: {e}"}
        log.info("outcomes | win=%s declined=%s closed=%s reason=%s",
                 outcomes.get("win"), outcomes.get("lost_declined"),
                 outcomes.get("lost_closed"), outcomes.get("reason"))

    # Refresh the connects balance on the tick that will send the daily report.
    # It is only written when the agent visits Upwork, so a report built from the
    # stored file alone can quote a figure that is days old. Must happen HERE,
    # inside the browser phase — the report block below runs after the lock is
    # released, and opening a browser there would race the UI.
    report_due = task_id is not None and prev.get("last_report_day") != _utc_today()
    if have_lock and probe.get("ok") and report_due:
        try:
            from connects import fetch_balance_live

            bal = fetch_balance_live()
            log.info("balance | ok=%s value=%s", bal.get("ok"), bal.get("balance"))
        except Exception as e:  # noqa: BLE001 — a stale balance must not skip the report
            log.warning("balance refresh failed: %s", e)

    # Browser phase done — release the lock so the UI can use the profile.
    if have_lock:
        _lock_release()

    fields = dict(
        state="running",
        heartbeat=_now(),
        tick_count=tick_no,
        active_task=label,
        session_ok=probe.get("ok"),
        session_reason=probe.get("reason"),
        last_ingest_added=ingest.get("added", 0),
        last_ingest_skipped=ingest.get("skipped_dup", 0),
        last_ingest_reason=ingest.get("reason"),
        last_search_added=search.get("added", 0),
        last_search_reason=search.get("reason"),
        last_qualify_applied=qualify.get("applied", 0),
        last_qualify_skipped=qualify.get("skipped", 0),
        last_qualify_errors=qualify.get("errors", 0),
        last_qualify_reason=qualify.get("reason"),
        last_draft_drafted=draft.get("drafted", 0),
        last_draft_errors=draft.get("errors", 0),
        last_draft_reason=draft.get("reason"),
        last_submit_submitted=submit.get("submitted", 0),
        last_submit_reason=submit.get("reason"),
        last_msg_imported=msg.get("imported", 0),
        last_msg_reason=msg.get("reason"),
        last_outcomes_win=outcomes.get("win", 0),
        last_outcomes_lost=(outcomes.get("lost_declined", 0) or 0) + (outcomes.get("lost_closed", 0) or 0),
        last_outcomes_reason=outcomes.get("reason"),
    )
    try:
        from ai import LLM_STATS

        fields["llm_calls"] = LLM_STATS["calls"]
        fields["llm_retries"] = LLM_STATS["retries"]
        fields["llm_errors"] = LLM_STATS["errors"]
    except Exception:  # noqa: BLE001
        pass
    fields["anomalies"] = detect_anomalies(fields)

    # Alert on NEW anomalies. "Changed since last tick" alone is not enough:
    # a flapping session (down -> up -> down) or a worker restart makes the set
    # differ again and again, which is how the same session_down alert got sent
    # four times in an hour. Re-send an UNCHANGED set only after a cooldown.
    if fields["anomalies"]:
        if should_alert(prev, fields["anomalies"]):
            try:
                from reporting import send_alert

                send_alert("worker anomalies: " + ", ".join(fields["anomalies"]))
                fields["alerted_anomalies"] = fields["anomalies"]
                fields["alerted_at"] = _now_ts()
            except Exception:  # noqa: BLE001
                pass
        else:
            # Keep the existing alert stamp so the cooldown actually elapses.
            fields["alerted_anomalies"] = prev.get("alerted_anomalies")
            fields["alerted_at"] = prev.get("alerted_at")

    # Send the daily report once per calendar day. It fires on the FIRST tick of
    # a new UTC day, so the day it should describe is the one that just ended —
    # reporting the day that is minutes old would show zeros every morning.
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    reported_day = report_day_for(now)
    if task_id is not None and prev.get("last_report_day") != today:
        try:
            from analytics import metrics_scope_task_id
            from reporting import send_report

            # Same scope as the Dashboard — reporting only the active task made
            # the Telegram figures disagree with the screen they get checked
            # against as soon as a second task existed.
            rep = send_report(task_id=metrics_scope_task_id(), day=reported_day)
            # Only mark the day done if a channel actually delivered, so a
            # transient Telegram failure doesn't skip the report all day.
            if rep.get("sent"):
                fields["last_report_day"] = today
            log.info("report | delivered=%s telegram=%s discord=%s",
                     rep.get("sent"), rep.get("telegram"), rep.get("discord"))
        except Exception as e:  # noqa: BLE001
            log.warning("report failed: %s", e)

    log.info("tick end | anomalies=%s llm_calls=%s", fields["anomalies"], fields.get("llm_calls"))
    return write_status(**fields)


def safe_tick() -> dict:
    """Run one tick; never raise — log the full traceback and keep the loop alive."""
    try:
        return tick()
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error("TICK CRASHED: %s\n%s", e, tb)
        return write_status(state="running", heartbeat=_now(),
                            last_error=f"{type(e).__name__}: {e}")


def _handle_signal(signum, frame):  # noqa: ARG001
    global _running
    _running = False


def run(interval: int = DEFAULT_INTERVAL, once: bool = False) -> int:
    setup_logging()
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (ValueError, AttributeError):  # SIGTERM may be unavailable on Windows
        pass

    write_status(state="starting", pid=os.getpid(), interval=interval)
    log.info("=== worker started pid=%s interval=%ss ===", os.getpid(), interval)

    # Startup self-heal: a previous force-killed run can leave a zombie browser
    # holding the Edge profile. Clean it up (unless the UI currently holds the lock).
    try:
        from browser_lock import is_busy

        if not is_busy():
            from browser_cleanup import cleanup_profile
            from upwork_connect import pick_profile_dir

            info = cleanup_profile(pick_profile_dir())
            if info.get("killed") or info.get("removed_locks"):
                log.info("startup self-heal: %s", info)
    except Exception as e:  # noqa: BLE001 — never block startup on cleanup
        log.warning("startup self-heal skipped: %s", e)

    while _running:
        safe_tick()
        if once:
            break
        for _ in range(interval):  # 1s slices → responsive Ctrl+C
            if not _running:
                break
            time.sleep(1)

    write_status(state="stopped")
    log.info("=== worker stopped ===")
    return 0


if __name__ == "__main__":
    setup_logging()
    sys.exit(run(once="--once" in sys.argv))
