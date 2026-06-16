"""Day 4 verification — qualification (APPLY/SKIP).

Covers the spec's Verification mapping:
- robust JSON parsing (plain, fenced, prose-wrapped, invalid).
- qualify_job: APPLY → READY_TO_PROPOSE, SKIP → SKIPPED, logs analysis JSON.
- invalid JSON twice → status ERROR.
- 3 descriptions → 1 SKIP, 2 APPLY (batch over qualify_new_jobs).
"""

import json
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base, Job, Task, Company  # noqa: E402
import qualify  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    TestSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(qualify, "get_db_session", TestSession)
    session = TestSession()
    yield session
    session.close()


def test_parse_plain_json():
    out = qualify.parse_qualify_response(
        '{"decision":"APPLY","fit_score":80,"risk_score":20,"skip_reason":null,"summary":"good"}'
    )
    assert out["decision"] == "APPLY"
    assert out["fit_score"] == 80
    assert out["skip_reason"] is None


def test_parse_fenced_and_prose():
    fenced = qualify.parse_qualify_response(
        '```json\n{"decision":"SKIP","fit_score":10,"risk_score":90,"skip_reason":"low budget","summary":"junk"}\n```'
    )
    assert fenced["decision"] == "SKIP"
    assert fenced["skip_reason"] == "low budget"

    prose = qualify.parse_qualify_response(
        'Here is the result: {"decision":"APPLY","fit_score":55,"risk_score":40,"summary":"ok"} done'
    )
    assert prose["decision"] == "APPLY"


def test_parse_clamps_and_rejects():
    out = qualify.parse_qualify_response('{"decision":"APPLY","fit_score":150,"risk_score":-5,"summary":""}')
    assert out["fit_score"] == 100
    assert out["risk_score"] == 0
    with pytest.raises(ValueError):
        qualify.parse_qualify_response('{"decision":"MAYBE"}')
    with pytest.raises(ValueError):
        qualify.parse_qualify_response("not json at all")


def _make_job(db, title="Unity multiplayer", desc="Photon co-op", status="NEW", task_id=None):
    job = Job(title=title, description=desc, status=status, task_id=task_id)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_qualify_job_apply(db):
    job = _make_job(db)
    llm = lambda msgs: '{"decision":"APPLY","fit_score":85,"risk_score":15,"summary":"fits Unity"}'
    res = qualify.qualify_job(job, None, db, llm=llm)
    assert res["status"] == "READY_TO_PROPOSE"
    assert job.ai_decision == "APPLY"
    assert job.fit_score == 85
    assert json.loads(job.ai_analysis_json)["decision"] == "APPLY"


def test_qualify_job_skip(db):
    job = _make_job(db)
    llm = lambda msgs: '{"decision":"SKIP","fit_score":5,"risk_score":95,"skip_reason":"NFT scope","summary":"out of niche"}'
    res = qualify.qualify_job(job, None, db, llm=llm)
    assert res["status"] == "SKIPPED"
    assert job.skip_reason == "NFT scope"


def test_qualify_job_invalid_twice_errors(db):
    job = _make_job(db)
    llm = lambda msgs: "garbage, not json"
    res = qualify.qualify_job(job, None, db, llm=llm)
    assert res["status"] == "ERROR"
    assert job.status == "ERROR"


def test_batch_retries_error_jobs(db):
    """A job left in ERROR by a prior transient failure is re-qualified, not orphaned."""
    job = _make_job(db, title="VR Quest pilot", status="ERROR")
    llm = lambda msgs: '{"decision":"APPLY","fit_score":90,"risk_score":10,"summary":"good"}'
    res = qualify.qualify_new_jobs(None, db=db, llm=llm)
    assert res["total"] == 1
    assert res["applied"] == 1
    assert job.status == "READY_TO_PROPOSE"


def test_batch_one_skip_two_apply(db):
    company = Company(name="Acme")
    db.add(company)
    db.commit()
    task = Task(company_id=company.id, name="Unity", strategy="Unity multiplayer games", is_active=1)
    db.add(task)
    db.commit()
    db.refresh(task)

    _make_job(db, title="Unity co-op", task_id=task.id)
    _make_job(db, title="Unity PvP", task_id=task.id)
    _make_job(db, title="Casino slots", desc="online gambling slot machine", task_id=task.id)

    def fake_llm(messages):
        text = messages[1]["content"]
        if "gambling" in text:
            return '{"decision":"SKIP","fit_score":5,"risk_score":95,"skip_reason":"gambling","summary":"no"}'
        return '{"decision":"APPLY","fit_score":80,"risk_score":20,"summary":"yes"}'

    res = qualify.qualify_new_jobs(task.id, db=db, llm=fake_llm)
    assert res["applied"] == 2
    assert res["skipped"] == 1
    assert res["errors"] == 0


def test_qualify_all_tasks_uses_each_own_strategy(db):
    """task_id=None qualifies every NEW job against ITS OWN task's strategy."""
    company = Company(name="Acme")
    db.add(company)
    db.commit()
    unity = Task(company_id=company.id, name="Unity", strategy="Unity games only", is_active=0)
    web = Task(company_id=company.id, name="Web", strategy="React web apps only", is_active=0)
    db.add_all([unity, web])
    db.commit()
    db.refresh(unity)
    db.refresh(web)

    _make_job(db, title="Unity job", desc="build a Unity game", task_id=unity.id)
    _make_job(db, title="React job", desc="build a React site", task_id=web.id)

    def strategy_aware_llm(messages):
        text = messages[1]["content"]
        # APPLY only when the job tech matches the task strategy present in the prompt.
        if ("Unity games only" in text and "Unity" in text) or (
            "React web apps only" in text and "React" in text
        ):
            return '{"decision":"APPLY","fit_score":90,"risk_score":10,"summary":"match"}'
        return '{"decision":"SKIP","fit_score":10,"risk_score":80,"skip_reason":"mismatch","summary":"no"}'

    res = qualify.qualify_new_jobs(None, db=db, llm=strategy_aware_llm)
    assert res["total"] == 2
    assert res["applied"] == 2  # each matched its own task's strategy
    assert res["skipped"] == 0
