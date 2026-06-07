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
    assert "Spent connects: 28" in text
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
