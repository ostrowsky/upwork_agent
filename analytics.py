"""Funnel metrics and niche/budget analytics (ТЗ: connects/replies/interviews/hires/$).

Pure DB aggregation — unit-testable with an in-memory session.
"""
from __future__ import annotations

import json

from database import get_db_session, Job, Proposal, Client, ClientMessage, CaseStudy


def compute_metrics(db, task_id: int | None = None) -> dict:
    """Pipeline funnel for the dashboard.

    connects_spent / proposals_sent — from Proposal; interviews / hires / revenue
    — from Job.outcome; replies — clients with ≥1 inbound message.
    """
    jq = db.query(Job)
    if task_id is not None:
        jq = jq.filter(Job.task_id == task_id)
    jobs = jq.all()
    job_ids = [j.id for j in jobs] or [0]

    pq = db.query(Proposal).filter(Proposal.status == "SENT")
    if task_id is not None:
        pq = pq.filter(Proposal.job_id.in_(job_ids))
    sent = pq.all()

    replies = (
        db.query(ClientMessage.client_id)
        .filter(ClientMessage.direction == "inbound")
        .distinct()
        .count()
    )

    return {
        "proposals_sent": len(sent),
        "connects_spent": sum((p.connects_spent or 0) for p in sent),
        "replies": replies,
        "interviews": sum(1 for j in jobs if j.outcome == "INTERVIEW"),
        "hires": sum(1 for j in jobs if j.outcome == "WIN"),
        "revenue": sum((j.revenue or 0) for j in jobs if j.outcome == "WIN"),
        "lost": sum(1 for j in jobs if j.outcome == "LOST"),
    }


def _budget_bucket(budget: str | None) -> str:
    if not budget:
        return "unknown"
    b = budget.lower()
    if "fixed" in b:
        return "fixed-price"
    return "hourly"


def analytics_by_bucket(db, task_id: int | None = None, min_volume: int = 3) -> list[dict]:
    """Per-budget-type funnel with an underperformer flag.

    A bucket is flagged when it has ≥min_volume sent proposals but no hires,
    so the operator can deprioritise it (ТЗ: «флаг неэффективных категорий»).
    """
    jq = db.query(Job).filter(Job.outcome.isnot(None) | (Job.status == "SENT"))
    if task_id is not None:
        jq = jq.filter(Job.task_id == task_id)
    jobs = jq.all()

    buckets: dict[str, dict] = {}
    for j in jobs:
        key = _budget_bucket(j.budget)
        b = buckets.setdefault(key, {"bucket": key, "sent": 0, "win": 0, "lost": 0, "revenue": 0})
        if j.status == "SENT" or j.outcome:
            b["sent"] += 1
        if j.outcome == "WIN":
            b["win"] += 1
            b["revenue"] += j.revenue or 0
        elif j.outcome == "LOST":
            b["lost"] += 1

    out = []
    for b in buckets.values():
        b["win_rate"] = round(b["win"] / b["sent"], 2) if b["sent"] else 0.0
        b["underperforming"] = b["sent"] >= min_volume and b["win"] == 0
        out.append(b)
    out.sort(key=lambda x: (-x["win"], -x["sent"]))
    return out


def _budget_range_bucket(budget: str | None) -> str:
    """Human-readable money range (ТЗ: «Best budget range: $5k–$20k»)."""
    from submit import parse_money

    if budget and "hourly" in budget.lower():
        return "hourly"
    vals = [v for v in parse_money(budget) if v > 0]
    if not vals:
        return "unknown"
    top = max(vals)
    if top < 1000:
        return "<$1k"
    if top < 5000:
        return "$1k–$5k"
    if top < 20000:
        return "$5k–$20k"
    return "$20k+"


def _funnel_by_key(jobs, key_fn, min_volume: int) -> list[dict]:
    """Generic per-key funnel (sent/win/lost/revenue + win_rate + underperformer)."""
    groups: dict[str, dict] = {}
    for j in jobs:
        key = key_fn(j)
        g = groups.setdefault(key, {"key": key, "sent": 0, "win": 0, "lost": 0, "revenue": 0})
        if j.status == "SENT" or j.outcome:
            g["sent"] += 1
        if j.outcome == "WIN":
            g["win"] += 1
            g["revenue"] += j.revenue or 0
        elif j.outcome == "LOST":
            g["lost"] += 1
    out = []
    for g in groups.values():
        g["win_rate"] = round(g["win"] / g["sent"], 2) if g["sent"] else 0.0
        g["underperforming"] = g["sent"] >= min_volume and g["win"] == 0
        out.append(g)
    out.sort(key=lambda x: (-x["win"], -x["win_rate"], -x["sent"]))
    return out


def _outcome_jobs(db, task_id: int | None):
    jq = db.query(Job).filter(Job.outcome.isnot(None) | (Job.status == "SENT"))
    if task_id is not None:
        jq = jq.filter(Job.task_id == task_id)
    return jq.all()


def analytics_by_budget_range(db, task_id: int | None = None, min_volume: int = 3) -> list[dict]:
    """Per money-range funnel (ranges, not just fixed/hourly)."""
    return _funnel_by_key(_outcome_jobs(db, task_id), lambda j: _budget_range_bucket(j.budget), min_volume)


def analytics_by_niche(db, task_id: int | None = None, min_volume: int = 3) -> list[dict]:
    """Per-niche funnel. A job's niche = niche of the first case cited in its proposal.

    Falls back to 'unknown' when a job has no proposal/cited case or the case has no niche.
    """
    case_niche = {c.id: (c.niche or "").strip() for c in db.query(CaseStudy).all()}

    def niche_of(job) -> str:
        p = (
            db.query(Proposal).filter(Proposal.job_id == job.id, Proposal.status == "SENT").first()
            or db.query(Proposal).filter(Proposal.job_id == job.id).order_by(Proposal.id.desc()).first()
        )
        if not p or not p.selected_cases:
            return "unknown"
        try:
            ids = json.loads(p.selected_cases)
        except (ValueError, TypeError):
            return "unknown"
        for cid in ids:
            niche = case_niche.get(cid)
            if niche:
                return niche
        return "unknown"

    return _funnel_by_key(_outcome_jobs(db, task_id), niche_of, min_volume)


def dashboard_metrics(task_id: int | None = None) -> dict:
    db = get_db_session()
    try:
        return {"funnel": compute_metrics(db, task_id), "buckets": analytics_by_bucket(db, task_id)}
    finally:
        db.close()
