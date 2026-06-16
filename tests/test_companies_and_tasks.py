"""Day 1 verification — companies-and-tasks spec.

Covers the spec's Verification mapping:
- Company + Task creation persists.
- Task chat history (3 messages) is stored and read back in order.
- Job.task_id links a job to a task.
- At most one task is active for the worker.
"""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base, Company, Task, TaskMessage, Job  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    TestSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    session = TestSession()
    yield session
    session.close()


def test_company_task_and_chat_history(db):
    company = Company(name="Acme", upwork_email="acme@example.com")
    db.add(company)
    db.commit()
    db.refresh(company)

    task = Task(
        company_id=company.id,
        name="Unity games",
        description="Unity, $5k-$20k, no NFT",
        strategy="Unity, $5k-$20k, no NFT",
        is_active=1,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    for role, content in [
        ("user", "Focus on multiplayer Unity jobs"),
        ("assistant", "Understood: multiplayer Unity, $5k-$20k."),
        ("user", "Skip anything blockchain-related"),
    ]:
        db.add(TaskMessage(task_id=task.id, role=role, content=content))
    db.commit()

    msgs = (
        db.query(TaskMessage)
        .filter(TaskMessage.task_id == task.id)
        .order_by(TaskMessage.id.asc())
        .all()
    )
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0].content.startswith("Focus on multiplayer")
    assert len(msgs) == 3


def test_job_links_to_task(db):
    company = Company(name="Acme")
    db.add(company)
    db.commit()
    task = Task(company_id=company.id, name="Backend", is_active=1)
    db.add(task)
    db.commit()
    db.refresh(task)

    job = Job(task_id=task.id, title="Build API", description="FastAPI service")
    db.add(job)
    db.commit()
    db.refresh(job)

    assert job.task_id == task.id


def test_only_one_active_task(db):
    company = Company(name="Acme")
    db.add(company)
    db.commit()
    t1 = Task(company_id=company.id, name="A", is_active=1)
    t2 = Task(company_id=company.id, name="B", is_active=0)
    db.add_all([t1, t2])
    db.commit()

    # activate t2 the way the UI does
    db.query(Task).update({Task.is_active: 0})
    db.query(Task).filter(Task.id == t2.id).update({Task.is_active: 1})
    db.commit()

    active = db.query(Task).filter(Task.is_active == 1).all()
    assert len(active) == 1
    assert active[0].id == t2.id
