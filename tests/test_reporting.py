"""Day 10 — daily report build/format/save and channel delivery (mocked HTTP)."""

import json
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base, Job, Proposal, DailyReport  # noqa: E402
import reporting  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    TestSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(reporting, "get_db_session", TestSession)
    session = TestSession()
    yield session
    session.close()


def _seed(db):
    j1 = Job(title="a", description="d", status="SENT", budget="Fixed-price", outcome="WIN", revenue=14000)
    j2 = Job(title="b", description="d", status="SENT", budget="Hourly: $30", outcome="LOST")
    db.add_all([j1, j2])
    db.commit()
    db.add_all([
        Proposal(job_id=j1.id, status="SENT", content="x", connects_spent=16),
        Proposal(job_id=j2.id, status="SENT", content="x", connects_spent=12),
    ])
    db.commit()


def test_build_and_format(db):
    _seed(db)
    m = reporting.build_report(db)
    assert m["connects_spent"] == 28
    assert m["revenue"] == 14000
    assert m["revenue_per_connect"] == round(14000 / 28, 2)
    assert m["best_bucket"] == "fixed-price"
    assert "hourly" in m["underperforming"] or m["underperforming"] == []  # 1 hourly sent < min_volume

    text = reporting.format_report_text(m, day="2026-06-05")
    # Seeded proposals carry no submitted_at, so they count toward the all-time
    # total but not toward any single day.
    assert "Spent connects: 0 (всего: 28)" in text
    assert "Proposals sent: 0 (всего: 2)" in text
    assert "Hires: 1" in text
    assert "Revenue booked: $14,000" in text
    assert "Revenue / connect:" in text
    assert "Best niche:" in text
    assert "Worst niche:" in text
    assert "Best budget range:" in text


def test_save_report_is_one_per_day(db):
    _seed(db)
    m = reporting.build_report(db)
    reporting.save_report(db, m, "txt", day="2026-06-05")
    reporting.save_report(db, m, "txt2", day="2026-06-05")
    reps = db.query(DailyReport).filter(DailyReport.day == "2026-06-05").all()
    assert len(reps) == 1  # upsert, not duplicate
    assert reps[0].text == "txt2"


def test_send_report_delivers_and_saves(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(reporting, "send_telegram", lambda t: (True, "telegram 200"))
    monkeypatch.setattr(reporting, "send_discord", lambda t: (False, "discord not configured"))
    res = reporting.send_report(db=db, day="2026-06-05")
    assert res["ok"] and res["sent"] is True
    assert db.query(DailyReport).count() == 1


def test_send_telegram_not_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    ok, msg = reporting.send_telegram("hi")
    assert ok is False and "not configured" in msg


def test_report_counts_proposals_submitted_on_that_day(db):
    """The daily line must reflect what was actually sent that day.

    Regression: the report only ever showed all-time numbers, so a day with
    real submissions could still print 0 and read as "nothing was sent".
    """
    from datetime import datetime, timezone

    j = Job(title="a", description="d", status="SENT", budget="Fixed-price")
    db.add(j)
    db.commit()
    db.add_all([
        Proposal(job_id=j.id, status="SENT", content="x", connects_spent=12,
                 submitted_at=datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)),
        Proposal(job_id=j.id, status="SENT", content="x", connects_spent=6,
                 submitted_at=datetime(2026, 8, 18, 20, 30, tzinfo=timezone.utc)),
        # Different day — must NOT be counted in the 18th's figures.
        Proposal(job_id=j.id, status="SENT", content="x", connects_spent=99,
                 submitted_at=datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)),
    ])
    db.commit()

    m = reporting.build_report(db, day="2026-08-18")
    assert m["proposals_sent_today"] == 2
    assert m["connects_spent_today"] == 18          # 12 + 6, not 117
    assert m["proposals_sent"] == 3                 # all-time still counts every one

    text = reporting.format_report_text(m, day="2026-08-18")
    assert "Proposals sent: 2 (всего: 3)" in text
    assert "Spent connects: 18 (всего: 117)" in text


def test_worker_reports_the_day_that_just_ended():
    """The report fires on the first tick of a new UTC day, so it must describe
    the previous day — reporting the day that is minutes old showed zeros every
    morning, which is what made the report look broken."""
    from datetime import datetime, timezone

    import worker

    # 04:06 UTC on the 19th (when the real 'за 2026-08-19' report went out).
    assert worker.report_day_for(datetime(2026, 8, 19, 4, 6, tzinfo=timezone.utc)) == "2026-08-18"
    # Month and year boundaries must roll back correctly too.
    assert worker.report_day_for(datetime(2026, 9, 1, 0, 5, tzinfo=timezone.utc)) == "2026-08-31"
    assert worker.report_day_for(datetime(2027, 1, 1, 0, 1, tzinfo=timezone.utc)) == "2026-12-31"


def test_alert_is_labelled_with_the_instance(monkeypatch):
    """Several machines share one Telegram chat; an unlabelled alert gives no
    way to tell which installation is unhealthy."""
    monkeypatch.setenv("AGENT_INSTANCE_NAME", "HOME-PC")
    sent = {}

    def fake_telegram(text):
        sent["tg"] = text
        return True, "telegram 200"

    monkeypatch.setattr(reporting, "send_telegram", fake_telegram)
    monkeypatch.setattr(reporting, "send_discord", lambda t: (False, "discord not configured"))

    reporting.send_alert("worker anomalies: session_down")

    assert sent["tg"].startswith("🚨 [HOME-PC] ")
    assert "session_down" in sent["tg"]


def test_instance_name_prefers_explicit_override(monkeypatch):
    monkeypatch.setenv("AGENT_INSTANCE_NAME", "client-vm")
    assert reporting.instance_name() == "client-vm"


def test_instance_name_falls_back_to_hostname(monkeypatch):
    import socket

    monkeypatch.delenv("AGENT_INSTANCE_NAME", raising=False)
    assert reporting.instance_name() == socket.gethostname()


def test_report_header_names_the_instance(monkeypatch):
    monkeypatch.setenv("AGENT_INSTANCE_NAME", "HOME-PC")
    m = {"proposals_sent": 0, "connects_spent": 0, "replies": 0, "interviews": 0,
         "hires": 0, "revenue": 0, "revenue_per_connect": 0.0}
    text = reporting.format_report_text(m, day="2026-08-27")
    assert text.splitlines()[0] == "📊 Upwork отчёт за 2026-08-27 · HOME-PC"


def test_report_lists_the_jobs_that_were_applied_to(db):
    """A bare count doesn't tell the operator what the agent applied to."""
    from datetime import datetime, timezone

    j1 = Job(title="Unity VR developer", description="d", status="SENT", budget="Hourly")
    j2 = Job(title="Rebuild mobile battle royal", description="d", status="SENT", budget="Hourly")
    db.add_all([j1, j2])
    db.commit()
    db.add_all([
        Proposal(job_id=j1.id, status="SENT", content="x", connects_spent=27,
                 submitted_at=datetime(2026, 7, 18, 13, 0, tzinfo=timezone.utc)),
        Proposal(job_id=j2.id, status="SENT", content="x", connects_spent=11,
                 submitted_at=datetime(2026, 7, 18, 14, 0, tzinfo=timezone.utc)),
    ])
    db.commit()

    m = reporting.build_report(db, day="2026-07-18")
    text = reporting.format_report_text(m, day="2026-07-18")

    assert "Отклики отправлены (2):" in text
    assert "Unity VR developer" in text
    assert "27 cn" in text
    assert "Rebuild mobile battle royal" in text


def test_connects_fall_back_to_the_cost_read_from_the_form(db):
    """Upwork sometimes serves a Send button with no amount, leaving
    connects_spent NULL — a day of real submissions must not report 0 connects."""
    from datetime import datetime, timezone

    j = Job(title="a", description="d", status="SENT", budget="Hourly")
    db.add(j)
    db.commit()
    db.add(Proposal(job_id=j.id, status="SENT", content="x",
                    connects_spent=None, connects_cost=16,
                    submitted_at=datetime(2026, 7, 18, 13, 0, tzinfo=timezone.utc)))
    db.commit()

    m = reporting.build_report(db, day="2026-07-18")
    assert m["connects_spent_today"] == 16


def test_balance_line_shows_how_stale_the_figure_is(monkeypatch):
    """The stored balance only updates when the agent visits Upwork; unlabelled
    it silently reads as current."""
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    monkeypatch.setattr("connects.read_balance", lambda: {"balance": 144, "updated_at": old})
    m = {"proposals_sent": 0, "connects_spent": 0, "replies": 0, "interviews": 0,
         "hires": 0, "revenue": 0, "revenue_per_connect": 0.0}
    text = reporting.format_report_text(m, day="2026-08-27")
    assert "Connects balance: 144" in text
    assert "3 дн назад" in text


def test_fresh_balance_is_marked_current(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr("connects.read_balance",
                        lambda: {"balance": 78, "updated_at": datetime.now(timezone.utc).isoformat()})
    m = {"proposals_sent": 0, "connects_spent": 0, "replies": 0, "interviews": 0,
         "hires": 0, "revenue": 0, "revenue_per_connect": 0.0}
    assert "Connects balance: 78 (актуален)" in reporting.format_report_text(m, day="2026-08-27")


def test_balance_without_timestamp_is_not_labelled(monkeypatch):
    monkeypatch.setattr("connects.read_balance", lambda: {"balance": 10})
    m = {"proposals_sent": 0, "connects_spent": 0, "replies": 0, "interviews": 0,
         "hires": 0, "revenue": 0, "revenue_per_connect": 0.0}
    assert "Connects balance: 10\n" in reporting.format_report_text(m, day="2026-08-27") + "\n"
