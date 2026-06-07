"""Day 6 verification — proposal generation (7 blocks, EN/RU, cases, idempotent)."""

import json
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base, Job, Task, Company, CaseStudy, Proposal  # noqa: E402
import proposals  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    TestSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(proposals, "get_db_session", TestSession)
    session = TestSession()
    yield session
    session.close()


_FULL_JSON = json.dumps(
    {
        "understanding": "You need a Unity multiplayer mobile prototype with matchmaking.",
        "proof": "We shipped a real-time arena shooter [2] with Photon Fusion.",
        "approach": "Phase 1 lobby, Phase 2 gameplay loop, Phase 3 polish.",
        "questions": ["Target platforms?", "Preferred netcode?"],
        "risk_reducer": "Fixed-price milestones with a playable demo each phase.",
        "cta": "Shall we hop on a 15-min call this week?",
        "estimate": "Prototype $6k, MVP $12k.",
    }
)


def test_detect_language():
    assert proposals.detect_language("Need a Unity developer for mobile") == "EN"
    assert proposals.detect_language("Нужен Unity разработчик для мобильной игры") == "RU"
    assert proposals.detect_language("") == "EN"


def test_parse_valid_and_fenced():
    out = proposals.parse_proposal_response(_FULL_JSON)
    for k in proposals.REQUIRED_KEYS:
        assert k in out
    assert out["questions"] == ["Target platforms?", "Preferred netcode?"]

    fenced = proposals.parse_proposal_response(f"```json\n{_FULL_JSON}\n```")
    assert fenced["cta"]


def test_parse_missing_key_raises():
    with pytest.raises(ValueError):
        proposals.parse_proposal_response('{"understanding":"x"}')
    with pytest.raises(ValueError):
        proposals.parse_proposal_response("not json")


def test_assemble_has_all_blocks():
    data = proposals.parse_proposal_response(_FULL_JSON)
    text = proposals.assemble_proposal(data)
    assert "matchmaking" in text  # understanding
    assert "[2]" in text  # proof cites the case
    assert "Phase 1" in text  # approach
    assert "Questions:" in text and "Target platforms?" in text
    assert "milestones" in text  # risk reducer
    assert "call this week" in text  # cta


def _setup_job(db):
    company = Company(name="Acme")
    db.add(company)
    db.commit()
    task = Task(company_id=company.id, name="Unity", strategy="Unity multiplayer", is_active=1)
    db.add(task)
    db.commit()
    db.refresh(task)
    db.add(
        CaseStudy(
            id=2, title="Arena shooter", niche="Unity multiplayer mobile",
            stack="Unity, Photon", description="real-time pvp", result="shipped", deleted=0,
        )
    )
    job = Job(
        title="Unity multiplayer prototype",
        description="Need a Unity multiplayer mobile prototype with matchmaking",
        status="READY_TO_PROPOSE", task_id=task.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, task


def test_generate_creates_draft_and_is_idempotent(db):
    job, task = _setup_job(db)
    llm = lambda messages: _FULL_JSON

    r1 = proposals.generate_proposal(job, task, db, llm=llm)
    assert r1["status"] == "DRAFT"
    assert 2 in r1["cases"]  # selected the relevant case
    assert job.status == "PROPOSAL_DRAFTED"
    assert db.query(Proposal).filter(Proposal.job_id == job.id).count() == 1

    # Regenerate → reuse same DRAFT row, no duplicate.
    r2 = proposals.generate_proposal(job, task, db, llm=llm)
    assert r2["proposal_id"] == r1["proposal_id"]
    assert db.query(Proposal).filter(Proposal.job_id == job.id).count() == 1

    draft = db.query(Proposal).filter(Proposal.job_id == job.id).first()
    assert draft.estimate == "Prototype $6k, MVP $12k."
    assert json.loads(draft.selected_cases) == [2]


def test_generate_errors_on_bad_llm(db):
    job, task = _setup_job(db)
    res = proposals.generate_proposal(job, task, db, llm=lambda m: "garbage")
    assert res["status"] == "ERROR"


def test_batch_drafts_ready_jobs(db):
    job, task = _setup_job(db)
    res = proposals.generate_drafts_for_ready(task.id, db=db, llm=lambda m: _FULL_JSON)
    assert res["drafted"] == 1
    assert res["errors"] == 0
