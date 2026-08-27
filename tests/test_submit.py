"""Day 7 verification — submit guards (idempotency, url, daily cap, per-run).

The browser interaction is not exercised here; we test the pure logic that
protects against double-sends and connect burn.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base, Job, Proposal  # noqa: E402
import submit  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    TestSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(submit, "get_db_session", TestSession)
    session = TestSession()
    yield session
    session.close()


def test_build_apply_url():
    job = Job(title="t", description="d", upwork_job_id="021abcdef01234567")
    assert submit.build_apply_url(job) == "https://www.upwork.com/nx/proposals/job/~021abcdef01234567/apply/"

    job2 = Job(title="t", description="d", source_url="https://www.upwork.com/jobs/X_~021ffffffffffffffff/")
    assert "~021ffffffffffffffff/apply/" in submit.build_apply_url(job2)

    job3 = Job(title="t", description="d")
    assert submit.build_apply_url(job3) is None


def test_can_submit_guards():
    job = Job(id=1, title="t", description="d", upwork_job_id="021aaaaaaaaaaaaaaa", status="PROPOSAL_DRAFTED")

    assert submit.can_submit(job, None)[0] is False  # no proposal

    empty = Proposal(job_id=1, status="DRAFT", content="")
    assert submit.can_submit(job, empty)[0] is False  # empty content

    sent = Proposal(job_id=1, status="SENT", content="hi")
    assert submit.can_submit(job, sent)[0] is False  # already sent

    ok = Proposal(job_id=1, status="DRAFT", content="hello")
    assert submit.can_submit(job, ok) == (True, "ok")

    job.status = "SENT"
    assert submit.can_submit(job, ok)[0] is False  # job already sent


def test_submissions_today_counts_only_today(db):
    now = datetime.now(timezone.utc)
    db.add(Proposal(job_id=1, status="SENT", content="a", submitted_at=now))
    db.add(Proposal(job_id=2, status="SENT", content="b", submitted_at=now - timedelta(days=2)))
    db.add(Proposal(job_id=3, status="DRAFT", content="c"))  # never submitted
    db.commit()
    assert submit.submissions_today(db) == 1


def test_per_run_limit_default_one(monkeypatch):
    monkeypatch.delenv("SUBMIT_PER_RUN", raising=False)
    assert submit.submit_per_run() == 1
    monkeypatch.setenv("SUBMIT_PER_RUN", "5")
    assert submit.submit_per_run() == 5


def test_submit_ready_dry_run_caps_per_run(db, monkeypatch):
    monkeypatch.setenv("SUBMIT_PER_RUN", "1")
    monkeypatch.setenv("AUTO_SUBMIT", "0")
    # three drafted jobs ready to submit
    for i in range(1, 4):
        db.add(Job(id=i, title=f"j{i}", description="d", status="PROPOSAL_DRAFTED",
                   upwork_job_id=f"021{i:015d}", task_id=1))
        db.add(Proposal(job_id=i, status="DRAFT", content="hello"))
    db.commit()

    # Stub the browser layer: one fake context for the whole batch, count per-job calls.
    import contextlib

    import browser

    @contextlib.contextmanager
    def fake_browser_page():
        yield object()

    calls = {"n": 0}

    def fake_apply(page, url, job, proposal, db, dry_run):
        calls["n"] += 1
        return {"ok": True, "submitted": False, "dry_run": True, "reason": "stub", "connects": None}

    monkeypatch.setattr(browser, "browser_page", fake_browser_page)
    monkeypatch.setattr(submit, "_apply_on_page", fake_apply)
    res = submit.submit_ready(task_id=1, db=db, dry_run=True)
    assert calls["n"] == 1  # per-run cap of 1 honored → one apply call
    assert res["dry_run"] == 1
    assert res["total"] == 1


def test_submit_many_opens_one_browser_for_batch(db, monkeypatch):
    monkeypatch.setenv("AUTO_SUBMIT", "0")
    for i in range(1, 4):
        db.add(Job(id=i, title=f"j{i}", description="d", status="PROPOSAL_DRAFTED",
                   upwork_job_id=f"021{i:015d}", task_id=1))
        db.add(Proposal(job_id=i, status="DRAFT", content="hello"))
    db.commit()

    import contextlib

    import browser

    opens = {"n": 0}

    @contextlib.contextmanager
    def fake_browser_page():
        opens["n"] += 1
        yield object()

    monkeypatch.setattr(browser, "browser_page", fake_browser_page)
    monkeypatch.setattr(submit, "_apply_on_page",
                        lambda *a, **k: {"ok": True, "submitted": False, "dry_run": True,
                                         "reason": "stub", "connects": None})

    items = [(db.query(Job).filter(Job.id == i).first(),
              db.query(Proposal).filter(Proposal.job_id == i).first()) for i in (1, 2, 3)]
    r = submit.submit_many(items, db, dry_run=True)
    assert opens["n"] == 1   # ONE browser session for the whole batch (was N before)
    assert r["total"] == 3 and r["dry_run"] == 3


def test_daily_limit_zero_means_unlimited(monkeypatch):
    """The UI can switch the cap off so a batch run covers every ready job."""
    monkeypatch.setenv("DAILY_SUBMIT_LIMIT", "0")
    assert submit.daily_submit_limit() == 0


def test_daily_limit_reads_configured_value(monkeypatch):
    monkeypatch.setenv("DAILY_SUBMIT_LIMIT", "7")
    assert submit.daily_submit_limit() == 7


def test_daily_limit_falls_back_on_garbage(monkeypatch):
    """A typo must not read as 'unlimited' and drain the connects balance."""
    monkeypatch.setenv("DAILY_SUBMIT_LIMIT", "три")
    assert submit.daily_submit_limit() == 10


def _stub_batch(db, monkeypatch, n=3):
    """n drafted jobs + a browser/apply stub that reports every send as real."""
    import contextlib

    import browser

    for i in range(1, n + 1):
        db.add(Job(id=i, title=f"j{i}", description="d", status="PROPOSAL_DRAFTED",
                   upwork_job_id=f"021{i:015d}", task_id=1))
        db.add(Proposal(job_id=i, status="DRAFT", content="hello"))
    db.commit()

    @contextlib.contextmanager
    def fake_browser_page():
        yield object()

    monkeypatch.setattr(browser, "browser_page", fake_browser_page)
    monkeypatch.setattr(submit, "_apply_on_page",
                        lambda *a, **k: {"ok": True, "submitted": True, "dry_run": False,
                                         "reason": "sent", "connects": 4})
    return [(db.query(Job).filter(Job.id == i).first(),
             db.query(Proposal).filter(Proposal.job_id == i).first())
            for i in range(1, n + 1)]


def test_cap_stops_the_batch_partway(db, monkeypatch):
    monkeypatch.setenv("AUTO_SUBMIT", "1")
    items = _stub_batch(db, monkeypatch, n=3)
    r = submit.submit_many(items, db, dry_run=False, cap=2, already=0)
    assert r["submitted"] == 2
    assert r["skipped_cap"] == 1


def test_cap_of_zero_sends_every_ready_job(db, monkeypatch):
    """Autosubmit with the limit switched off must not stop at an implicit cap."""
    monkeypatch.setenv("AUTO_SUBMIT", "1")
    items = _stub_batch(db, monkeypatch, n=3)
    r = submit.submit_many(items, db, dry_run=False, cap=0, already=99)
    assert r["submitted"] == 3
    assert r["skipped_cap"] == 0
