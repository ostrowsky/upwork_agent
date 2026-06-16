"""Day 9 — outcomes, loss post-mortem, agent questions."""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base, Job, Task, Company, AgentQuestion, TaskMessage  # noqa: E402
import learning  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _task(db):
    c = Company(name="Acme")
    db.add(c)
    db.commit()
    t = Task(company_id=c.id, name="Unity", strategy="Unity games", is_active=1)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_set_outcome_win_records_revenue(db):
    t = _task(db)
    job = Job(title="j", description="d", status="SENT", task_id=t.id)
    db.add(job)
    db.commit()
    res = learning.set_outcome(db, job.id, "WIN", revenue=14000)
    assert res["ok"]
    db.refresh(job)
    assert job.outcome == "WIN" and job.revenue == 14000


def test_set_outcome_invalid(db):
    t = _task(db)
    job = Job(title="j", description="d", task_id=t.id)
    db.add(job)
    db.commit()
    assert learning.set_outcome(db, job.id, "MAYBE")["ok"] is False


def test_set_outcome_rejected_for_unsent_job(db):
    """An outcome on a SKIPPED/NEW (never-sent) job is nonsensical → rejected."""
    t = _task(db)
    job = Job(title="skipped", description="d", status="SKIPPED", task_id=t.id)
    db.add(job)
    db.commit()
    res = learning.set_outcome(db, job.id, "LOST", llm=lambda m: "{}")
    assert res["ok"] is False and "not sent" in res["reason"]
    db.refresh(job)
    assert job.outcome is None  # not polluted


def test_lost_triggers_postmortem_and_question(db):
    t = _task(db)
    job = Job(title="Unity multiplayer", description="build game", status="SENT",
              budget="Fixed-price", task_id=t.id)
    db.add(job)
    db.commit()

    llm = lambda m: '{"reason": "Ставка выше рынка", "recommendation": "Снизить бид на 15%"}'
    res = learning.set_outcome(db, job.id, "LOST", llm=llm)
    assert res["ok"]
    db.refresh(job)
    assert "Ставка выше рынка" in job.loss_reason
    assert "Снизить бид" in job.loss_reason
    # a question was raised for the operator
    qs = learning.list_questions(db, status="open")
    assert len(qs) == 1
    assert qs[0].task_id == t.id


def test_answer_question_feeds_task_chat(db):
    t = _task(db)
    q = learning.create_question(db, "Снизить ставку?", task_id=t.id)
    learning.answer_question(db, q.id, "Да, до $30/час")
    db.refresh(q)
    assert q.status == "answered"
    msgs = db.query(TaskMessage).filter(TaskMessage.task_id == t.id).all()
    assert any("до $30/час" in m.content for m in msgs)


def test_parse_loss_tolerates_fences():
    out = learning._parse_loss('```json\n{"reason":"r","recommendation":"rec"}\n```')
    assert out["reason"] == "r" and out["recommendation"] == "rec"
    assert learning._parse_loss("garbage")["reason"] == "Причина не определена."
