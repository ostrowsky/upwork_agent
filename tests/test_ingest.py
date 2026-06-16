"""Day 3 verification — job ingestion (dedup + normalize).

Covers the spec's Verification mapping for ingest:
- duplicate URL / upwork_job_id does not create a second Job.
- within-batch dedup.
- invalid (empty) records are rejected.
- upwork_job_id is derived from the URL.
"""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base, Job  # noqa: E402
import jobs  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    TestSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(jobs, "get_db_session", TestSession)
    session = TestSession()
    yield session
    session.close()


def test_extract_upwork_job_id():
    assert jobs.extract_upwork_job_id("https://www.upwork.com/jobs/~021abcdef0123456789") == "021abcdef0123456789"
    assert jobs.extract_upwork_job_id("https://www.upwork.com/nx/x?jobId=123456") == "123456"
    assert jobs.extract_upwork_job_id("https://example.com/none") is None
    assert jobs.extract_upwork_job_id(None) is None


def test_normalize_rejects_empty():
    assert jobs.normalize_record({"title": "", "description": "x"}) is None
    assert jobs.normalize_record({"title": "x", "description": ""}) is None
    rec = jobs.normalize_record(
        {"title": " Build API ", "description": " desc ", "source_url": "https://www.upwork.com/jobs/~0210000000000000"}
    )
    assert rec["title"] == "Build API"
    assert rec["upwork_job_id"] == "0210000000000000"


def test_ingest_dedup_by_job_id(db):
    rec = {
        "title": "Unity multiplayer",
        "description": "Photon co-op game",
        "source_url": "https://www.upwork.com/jobs/~021aaaaaaaaaaaaaaa",
    }
    r1 = jobs.ingest_jobs([rec], task_id=1, db=db)
    assert r1["added"] == 1
    # same job again (re-scrape) → skipped
    r2 = jobs.ingest_jobs([rec], task_id=1, db=db)
    assert r2["added"] == 0
    assert r2["skipped_dup"] == 1
    assert db.query(Job).count() == 1


def test_ingest_within_batch_dedup(db):
    rec = {
        "title": "Backend",
        "description": "FastAPI",
        "source_url": "https://www.upwork.com/jobs/~021bbbbbbbbbbbbbbb",
    }
    res = jobs.ingest_jobs([rec, dict(rec)], task_id=None, db=db)
    assert res["added"] == 1
    assert res["skipped_dup"] == 1


def test_ingest_counts_invalid(db):
    res = jobs.ingest_jobs(
        [{"title": "", "description": ""}, {"title": "ok", "description": "ok"}],
        task_id=None,
        db=db,
    )
    assert res["invalid"] == 1
    assert res["added"] == 1
