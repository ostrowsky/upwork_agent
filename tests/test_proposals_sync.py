"""Sync submitted-proposal status from Upwork — pure matching logic."""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, Job, Proposal  # noqa: E402
import proposals_sync as PS  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_parse_submitted_shapes():
    raw = [
        {"ciphertext": "~021aaaaaaaaaaaaaaaa", "title": "Job A"},
        {"job": {"ciphertext": "~021bbbbbbbbbbbbbbbb", "title": "Job B"}},
        {"jobUrl": "https://www.upwork.com/jobs/X_~021cccccccccccccccc/", "title": "Job C"},
        {"nothing": 1},
    ]
    recs = PS.parse_submitted(raw)
    assert {r["upwork_job_id"] for r in recs} == {
        "021aaaaaaaaaaaaaaaa", "021bbbbbbbbbbbbbbbb", "021cccccccccccccccc"
    }


def test_sync_marks_matching_job_sent(db):
    job = Job(title="VR Developer", description="d", status="PROPOSAL_DRAFTED",
              upwork_job_id="021dddddddddddddddd")
    db.add(job)
    db.commit()
    db.add(Proposal(job_id=job.id, status="DRAFT", content="x"))
    db.commit()

    res = PS.sync_submitted([{"upwork_job_id": "021dddddddddddddddd", "title": "VR Developer"}], db)
    assert res["matched"] == 1 and res["marked"] == 1
    db.refresh(job)
    assert job.status == "SENT"
    prop = db.query(Proposal).filter(Proposal.job_id == job.id).first()
    assert prop.status == "SENT" and prop.submitted_at is not None


def test_sync_matches_by_title_when_no_id(db):
    job = Job(title="Unity multiplayer", description="d", status="PROPOSAL_DRAFTED")
    db.add(job)
    db.commit()
    res = PS.sync_submitted([{"upwork_job_id": None, "title": "Unity multiplayer"}], db)
    assert res["marked"] == 1


def test_classify_outcome_mapping():
    assert PS.classify_outcome("You were hired") == ("WIN", "hired")
    assert PS.classify_outcome("Offer accepted") == ("WIN", "hired")
    assert PS.classify_outcome("You were declined") == ("LOST", "declined")
    assert PS.classify_outcome("Client passed on your proposal") == ("LOST", "declined")
    assert PS.classify_outcome("Job is closed") == ("LOST", "job closed")
    assert PS.classify_outcome("This job is no longer available") == ("LOST", "job closed")
    assert PS.classify_outcome("Proposal withdrawn") == (None, "")  # we withdrew → not a loss
    assert PS.classify_outcome("") == (None, "")
    assert PS.classify_outcome(None) == (None, "")


def test_parse_archived_collects_status_text():
    raw = [
        {"ciphertext": "~021aaaaaaaaaaaaaaaa", "title": "A", "status": "Declined"},
        {"job": {"ciphertext": "~021bbbbbbbbbbbbbbbb", "title": "B", "status": "Job is closed"}},
        {"title": "C", "statusLabel": {"label": "Hired"}},  # nested dict label shape
    ]
    recs = {r["title"]: r for r in PS.parse_archived(raw)}
    assert "Declined" in recs["A"]["status_text"]
    assert "closed" in recs["B"]["status_text"].lower()
    assert "Hired" in recs["C"]["status_text"]


def _sent_job(db, jid, title):
    job = Job(title=title, description="d", status="SENT", upwork_job_id=jid)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_sync_outcomes_sets_win_declined_closed(db):
    j_win = _sent_job(db, "021aaaaaaaaaaaaaaaa", "Won job")
    j_dec = _sent_job(db, "021bbbbbbbbbbbbbbbb", "Declined job")
    j_cls = _sent_job(db, "021cccccccccccccccc", "Closed job")
    llm_calls = {"n": 0}

    def llm(messages):
        llm_calls["n"] += 1
        return '{"reason": "weak proof", "recommendation": ""}'

    res = PS.sync_outcomes([
        {"upwork_job_id": "021aaaaaaaaaaaaaaaa", "title": "Won job", "status_text": "Hired"},
        {"upwork_job_id": "021bbbbbbbbbbbbbbbb", "title": "Declined job", "status_text": "You were declined"},
        {"upwork_job_id": "021cccccccccccccccc", "title": "Closed job", "status_text": "Job is closed"},
    ], db, llm=llm)

    assert res == {"matched": 3, "win": 1, "lost_declined": 1, "lost_closed": 1,
                   "skipped": 0, "unmatched": 0}
    db.refresh(j_win); db.refresh(j_dec); db.refresh(j_cls)
    assert j_win.outcome == "WIN"
    assert j_dec.outcome == "LOST" and "weak proof" in (j_dec.loss_reason or "")
    assert j_cls.outcome == "LOST" and "закрыт" in (j_cls.loss_reason or "")
    assert llm_calls["n"] == 1  # post-mortem ONLY for declined, not for closed


def test_sync_outcomes_idempotent_and_guards(db):
    j = _sent_job(db, "021dddddddddddddddd", "Already done")
    j.outcome = "WIN"
    draft = Job(title="Not sent", description="d", status="PROPOSAL_DRAFTED",
                upwork_job_id="021eeeeeeeeeeeeeeee")
    db.add(draft)
    db.commit()

    res = PS.sync_outcomes([
        {"upwork_job_id": "021dddddddddddddddd", "title": "Already done", "status_text": "Job is closed"},
        {"upwork_job_id": "021eeeeeeeeeeeeeeee", "title": "Not sent", "status_text": "Declined"},
        {"upwork_job_id": "021ffffffffffffffff", "title": "Ghost", "status_text": "Hired"},
    ], db, llm=lambda m: "{}")

    assert res["skipped"] == 2      # outcome already set + not SENT
    assert res["unmatched"] == 1
    db.refresh(j)
    assert j.outcome == "WIN"       # existing outcome never overwritten


def test_sync_idempotent_and_unmatched(db):
    job = Job(title="A", description="d", status="SENT", upwork_job_id="021eeeeeeeeeeeeeeee")
    db.add(job)
    db.commit()
    res = PS.sync_submitted([
        {"upwork_job_id": "021eeeeeeeeeeeeeeee", "title": "A"},   # already SENT → not re-marked
        {"upwork_job_id": "021ffffffffffffffff", "title": "Ghost"},  # no such job
    ], db)
    assert res["matched"] == 1
    assert res["marked"] == 0
    assert res["unmatched"] == 1
