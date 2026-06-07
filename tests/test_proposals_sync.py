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
