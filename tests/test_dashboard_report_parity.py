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


def test_report_links_to_every_job_it_lists(db):
    """A job number and title aren't actionable — the operator needs the link."""
    _seed_two_tasks(db)
    db.query(Job).filter(Job.id == 1).update(
        {"source_url": "https://www.upwork.com/jobs/~0211111111111111111/"})
    db.commit()

    text = reporting.format_report_text(
        reporting.build_report(db, day="2026-08-20"), day="2026-08-20")

    assert "https://www.upwork.com/jobs/~0211111111111111111/" in text


def test_quiet_day_still_lists_recent_proposals(db):
    """A day with no sends produced a wall of zeros and no way to reach
    anything that WAS sent."""
    _seed_two_tasks(db)
    text = reporting.format_report_text(
        reporting.build_report(db, day="2026-08-25"), day="2026-08-25")  # nothing that day

    assert "Proposals sent: 0" in text
    assert "Последние отправленные" in text
    assert "unity job" in text


def test_agent_chat_funnel_uses_the_shared_scope():
    """The agent quoted 11 proposals / 147 connects while the Dashboard beside
    it showed 12 / 167, because the chat context scoped the funnel to the active
    task. app.py is a Streamlit script and cannot be imported in a test, so the
    wiring is asserted at source level — the invariant is what matters.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "app.py"
    text = src.read_text(encoding="utf-8")

    assert "compute_metrics(db, tid)" not in text, \
        "agent chat is scoping the funnel to the active task again"
    assert "metrics_scope_task_id()" in text, \
        "agent chat must take its scope from the shared helper"


def test_batch_buttons_count_and_act_on_the_same_scope():
    """Autosubmit advertised "по всем готовым вакансиям (19)" while the list
    right below it showed 23 — the button counted only the active task's jobs.
    A count and the action it labels must describe the same set.

    app.py is a Streamlit script and cannot be imported, so the wiring is
    asserted at source level.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    block = src[src.index("_ready_n = sum("):]
    block = block[:block.index("_bsr = st.session_state.get")]

    assert "_scope is None or j.task_id == _scope" in block, \
        "the ready-jobs count is scoped differently from the job list again"
    assert "generate_drafts_for_ready(_scope" in block, "drafting must use the counted scope"
    assert "submit_ready(_scope" in block, "submitting must use the counted scope"
    assert "active.id" not in block, "batch actions must not silently narrow to the active task"
