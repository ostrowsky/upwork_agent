"""Day 9 — funnel metrics and niche/budget analytics."""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json  # noqa: E402

import database  # noqa: E402
from database import Base, Job, Proposal, Client, ClientMessage, CaseStudy  # noqa: E402
import analytics  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _job(db, **kw):
    j = Job(title=kw.pop("title", "t"), description="d", **kw)
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def test_compute_metrics_funnel(db):
    j1 = _job(db, status="SENT", budget="Hourly: $30-$50", outcome="WIN", revenue=14000)
    j2 = _job(db, status="SENT", budget="Fixed-price", outcome="INTERVIEW")
    j3 = _job(db, status="SENT", budget="Hourly", outcome="LOST")
    db.add_all([
        Proposal(job_id=j1.id, status="SENT", content="x", connects_spent=16),
        Proposal(job_id=j2.id, status="SENT", content="x", connects_spent=10),
        Proposal(job_id=j3.id, status="SENT", content="x", connects_spent=12),
    ])
    # two clients, one with an inbound reply
    c = Client(name="Acme")
    db.add(c)
    db.commit()
    db.add(ClientMessage(client_id=c.id, direction="inbound", content="hi"))
    db.commit()

    m = analytics.compute_metrics(db)
    assert m["proposals_sent"] == 3
    assert m["connects_spent"] == 38
    assert m["hires"] == 1
    assert m["interviews"] == 1
    assert m["lost"] == 1
    assert m["revenue"] == 14000
    assert m["replies"] == 1


def test_analytics_by_bucket_flags_underperformers(db):
    # hourly: 3 sent, 0 win → underperforming
    for _ in range(3):
        _job(db, status="SENT", budget="Hourly: $20", outcome="LOST")
    # fixed: 1 sent, 1 win
    _job(db, status="SENT", budget="Fixed-price", outcome="WIN", revenue=5000)

    buckets = {b["bucket"]: b for b in analytics.analytics_by_bucket(db, min_volume=3)}
    assert buckets["hourly"]["underperforming"] is True
    assert buckets["hourly"]["win"] == 0
    assert buckets["fixed-price"]["underperforming"] is False
    assert buckets["fixed-price"]["win_rate"] == 1.0


def test_budget_range_buckets(db):
    j1 = _job(db, status="SENT", budget="Fixed-price: $8,000", outcome="WIN", revenue=8000)
    j2 = _job(db, status="SENT", budget="Fixed-price: $500", outcome="LOST")
    rows = {r["key"]: r for r in analytics.analytics_by_budget_range(db)}
    assert rows["$5k–$20k"]["win"] == 1
    assert rows["<$1k"]["lost"] == 1


def test_analytics_by_niche(db):
    win_case = CaseStudy(title="Unity MP", niche="Unity multiplayer", description="d")
    lose_case = CaseStudy(title="NFT", niche="NFT games", description="d")
    db.add_all([win_case, lose_case])
    db.commit()
    jw = _job(db, status="SENT", budget="Fixed-price: $10,000", outcome="WIN", revenue=10000)
    jl = _job(db, status="SENT", budget="Fixed-price: $6,000", outcome="LOST")
    db.add_all([
        Proposal(job_id=jw.id, status="SENT", content="x", selected_cases=json.dumps([win_case.id])),
        Proposal(job_id=jl.id, status="SENT", content="x", selected_cases=json.dumps([lose_case.id])),
    ])
    db.commit()
    rows = {r["key"]: r for r in analytics.analytics_by_niche(db)}
    assert rows["Unity multiplayer"]["win"] == 1
    assert rows["NFT games"]["lost"] == 1
