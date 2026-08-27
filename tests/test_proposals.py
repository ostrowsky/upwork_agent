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


def test_proposal_always_cites_own_synthetic_case(db):
    """The case fabricated for THIS job (the attached PDF) must be cited in proof."""
    job, task = _setup_job(db)
    # A synthetic case tailored to this job — must win a slot even if another case
    # scores higher on tokens.
    db.add(CaseStudy(
        id=99, title="Tailored case", niche="generic", stack="x",
        description="made for this job", result="ok", deleted=0,
        synthetic=1, job_id=job.id,
    ))
    db.commit()

    res = proposals.generate_proposal(job, task, db, llm=lambda m: _FULL_JSON)
    assert 99 in res["cases"]  # own synthetic case is always cited


def test_generate_errors_on_bad_llm(db):
    job, task = _setup_job(db)
    res = proposals.generate_proposal(job, task, db, llm=lambda m: "garbage")
    assert res["status"] == "ERROR"


def test_batch_drafts_ready_jobs(db):
    job, task = _setup_job(db)
    res = proposals.generate_drafts_for_ready(task.id, db=db, llm=lambda m: _FULL_JSON)
    assert res["drafted"] == 1
    assert res["errors"] == 0


def test_cases_block_carries_the_narrative():
    """The letter can only argue transferable experience if the write-up
    reaches the prompt, not just the case title."""
    import types

    case = types.SimpleNamespace(
        id=7, title="Co-op slice", niche="Unity", stack="Photon",
        budget_range="$10k", result="Publisher signed",
        narrative="We chose client-side prediction over lockstep because one slow peer stalls everyone.",
    )
    block = proposals._cases_block([case])
    assert "Co-op slice" in block
    assert "подробнее:" in block
    assert "client-side prediction" in block


def test_cases_block_without_narrative_is_unchanged():
    import types

    case = types.SimpleNamespace(
        id=7, title="Co-op slice", niche="Unity", stack="Photon",
        budget_range="$10k", result="Publisher signed", narrative=None)
    block = proposals._cases_block([case])
    assert "подробнее:" not in block


def test_cases_block_truncates_a_long_narrative():
    """A long case must not crowd the job description out of the prompt."""
    import types

    case = types.SimpleNamespace(
        id=7, title="T", niche=None, stack=None, budget_range=None, result=None,
        narrative="x" * 5000)
    block = proposals._cases_block([case])
    assert len(block) < 1200
