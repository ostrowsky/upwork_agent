"""The Dashboard and the daily report must never quote different numbers.

They are two renderings of one funnel, checked against each other by the
operator. They used to disagree by construction: the Dashboard counted every
task while the worker reported only the active one, so the Telegram figures
diverged from the screen as soon as a second task existed.
"""
import os
import sys
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics  # noqa: E402
import database  # noqa: E402
import reporting  # noqa: E402
from database import Base, Company, Job, Proposal, Task  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    TestSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(analytics, "get_db_session", TestSession)
    monkeypatch.setattr(reporting, "get_db_session", TestSession)
    session = TestSession()
    yield session
    session.close()


def _seed_two_tasks(db):
    """Work spread over TWO tasks — the case where a scope mismatch shows up."""
    db.add(Company(id=1, name="Studio"))
    db.commit()
    db.add_all([Task(id=1, company_id=1, name="Unity", is_active=1),
                Task(id=2, company_id=1, name="Art", is_active=0)])
    db.commit()
    jobs = [
        Job(id=1, title="unity job", description="d", status="SENT",
            budget="Hourly", task_id=1, outcome="WIN", revenue=5000),
        Job(id=2, title="art job", description="d", status="SENT",
            budget="Fixed-price", task_id=2),
    ]
    db.add_all(jobs)
    db.commit()
    db.add_all([
        Proposal(job_id=1, status="SENT", content="x", connects_spent=16,
                 submitted_at=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)),
        Proposal(job_id=2, status="SENT", content="x", connects_spent=9,
                 submitted_at=datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc)),
    ])
    db.commit()


FUNNEL_KEYS = ("proposals_sent", "connects_spent", "replies",
               "interviews", "hires", "revenue", "lost")


def test_dashboard_and_report_agree_across_tasks(db):
    _seed_two_tasks(db)

    dash = analytics.dashboard_metrics()["funnel"]
    rep = reporting.build_report(db, day="2026-08-20")

    for k in FUNNEL_KEYS:
        assert dash[k] == rep[k], f"{k}: dashboard {dash[k]} != report {rep[k]}"
    # Both must see BOTH tasks — 16 + 9, not one task's share.
    assert dash["connects_spent"] == 25
    assert dash["proposals_sent"] == 2


def test_report_text_quotes_the_dashboard_totals(db):
    _seed_two_tasks(db)

    dash = analytics.dashboard_metrics()["funnel"]
    rep = reporting.build_report(db, day="2026-08-20")
    text = reporting.format_report_text(rep, day="2026-08-20")

    # The "(всего: N)" figures are literally what the Dashboard shows.
    assert f"(всего: {dash['proposals_sent']})" in text
    assert f"(всего: {dash['connects_spent']})" in text
    assert f"Hires: {dash['hires']}" in text
    assert f"Revenue booked: ${dash['revenue']:,}" in text


def test_both_read_the_same_scope_helper(monkeypatch):
    """Guard the wiring itself: if either side stops asking metrics_scope_task_id
    for its default, they can drift apart again without any test noticing."""
    calls = []
    monkeypatch.setattr(analytics, "metrics_scope_task_id",
                        lambda: (calls.append("analytics"), None)[1])
    monkeypatch.setattr(reporting, "metrics_scope_task_id",
                        lambda: (calls.append("reporting"), None)[1])

    analytics.dashboard_metrics()
    db = database.SessionLocal()
    try:
        reporting.build_report(db)
    finally:
        db.close()

    assert "analytics" in calls and "reporting" in calls


def test_day_scoped_figures_never_exceed_the_totals(db):
    """A day's numbers are a subset of all time; if the day filter ever broke
    open, the report would claim more sent today than exist in total."""
    _seed_two_tasks(db)
    rep = reporting.build_report(db, day="2026-08-20")
    assert rep["proposals_sent_today"] <= rep["proposals_sent"]
    assert rep["connects_spent_today"] <= rep["connects_spent"]
    assert len(rep["sent_jobs_today"]) == rep["proposals_sent_today"]
