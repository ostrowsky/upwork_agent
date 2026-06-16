"""Outcome tracking, loss post-mortem, and agent questions (Day 9).

When a job is marked LOST, the LLM analyses why and what to change; the result
is stored on the job and surfaced as an agent question for the operator. The
operator answers questions in the UI; answers flow into the task chat so future
proposals improve.
"""
from __future__ import annotations

from database import get_db_session, Job, Task, AgentQuestion  # noqa: F401 — get_db_session is a test monkeypatch seam


OUTCOMES = {"REPLIED", "INTERVIEW", "WIN", "LOST"}

LOSS_SYSTEM = (
    "Ты — аналитик продаж на Upwork. По вакансии, нашему отклику и стратегии "
    "задачи кратко объясни вероятную причину проигрыша и дай 1–2 ТАКТИЧЕСКИЕ "
    "рекомендации В РАМКАХ текущей ниши/стека стратегии (текст отклика, кейсы, "
    "ставка, уточняющие вопросы, скорость ответа). "
    "ЗАПРЕЩЕНО предлагать смену основного стека/ниши стратегии (например уход с "
    "Unity на Unreal) из-за одного проигрыша — один кейс не повод разворачивать "
    "стратегию. Если вакансия была не нашего стека — рекомендация: НЕ откликаться "
    "на такие (улучшить фильтр квалификации), а не менять нишу. Верни СТРОГО JSON: "
    '{"reason": str, "recommendation": str}. Язык — русский.'
)


# --------------------------------------------------------------------------
# Agent questions
# --------------------------------------------------------------------------

def create_question(db, text: str, task_id: int | None = None, job_id: int | None = None):
    q = AgentQuestion(text=text, task_id=task_id, job_id=job_id, status="open")
    db.add(q)
    db.commit()
    db.refresh(q)
    return q


def list_questions(db, status: str | None = None):
    q = db.query(AgentQuestion)
    if status:
        q = q.filter(AgentQuestion.status == status)
    return q.order_by(AgentQuestion.created_at.desc(), AgentQuestion.id.desc()).all()


def open_questions_count(db) -> int:
    return db.query(AgentQuestion).filter(AgentQuestion.status == "open").count()


def answer_question(db, question_id: int, answer: str):
    """Answer a question; the answer feeds back into the task chat (invariant)."""
    q = db.query(AgentQuestion).filter(AgentQuestion.id == question_id).first()
    if not q:
        return None
    q.answer = answer
    q.status = "answered"
    db.commit()
    # Persist into the task conversation so it shapes future generations.
    if q.task_id:
        from database import TaskMessage

        db.add(TaskMessage(task_id=q.task_id, role="user",
                           content=f"[Ответ на вопрос агента] {q.text}\n→ {answer}"))
        db.commit()
    return q


# --------------------------------------------------------------------------
# Outcomes & loss learning
# --------------------------------------------------------------------------

def _parse_loss(text: str) -> dict:
    import json
    import re

    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
    return {
        "reason": str(data.get("reason", "")).strip() or "Причина не определена.",
        "recommendation": str(data.get("recommendation", "")).strip(),
    }


def analyze_loss(job: Job, task: Task | None, db, llm=None) -> dict:
    """LLM post-mortem for a LOST job → store loss_reason + raise a question."""
    if llm is None:
        from ai import call_llm as llm

    strategy = ((task.strategy or task.description) if task else "") or "—"
    proposal_text = ""
    if job.proposals:
        proposal_text = (job.proposals[0].content or "")[:1500]

    user = (
        f"СТРАТЕГИЯ:\n{strategy}\n\nВАКАНСИЯ:\n{job.title}\nБюджет: {job.budget or '—'}\n"
        f"{(job.description or '')[:1200]}\n\nНАШ ОТКЛИК:\n{proposal_text or '—'}\n\nВерни JSON."
    )
    try:
        parsed = _parse_loss(llm([{"role": "system", "content": LOSS_SYSTEM},
                                  {"role": "user", "content": user}]))
    except Exception as e:  # noqa: BLE001
        parsed = {"reason": f"[Ошибка анализа: {e}]", "recommendation": ""}

    job.loss_reason = parsed["reason"] + (
        f"\nРекомендация: {parsed['recommendation']}" if parsed["recommendation"] else ""
    )
    db.commit()

    if parsed["recommendation"]:
        create_question(
            db,
            text=f"Проиграли «{job.title[:60]}». {parsed['reason']} "
                 f"Применить рекомендацию к стратегии? — {parsed['recommendation']}",
            task_id=job.task_id, job_id=job.id,
        )
    return parsed


def set_outcome(db, job_id: int, outcome: str, revenue: int | None = None, llm=None) -> dict:
    """Mark a SENT job's outcome. LOST triggers a post-mortem; WIN records revenue.

    Guard: an outcome only makes sense for a proposal we actually sent. Setting
    win/lost on a NEW/SKIPPED job is nonsensical and pollutes the funnel, so it
    is rejected (a SENT job, or one with a SENT proposal, is required).
    """
    from database import Proposal

    if outcome not in OUTCOMES:
        return {"ok": False, "reason": f"invalid outcome: {outcome}"}
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return {"ok": False, "reason": "job not found"}
    sent = job.status == "SENT" or db.query(Proposal).filter(
        Proposal.job_id == job_id, Proposal.status == "SENT").first() is not None
    if not sent:
        return {"ok": False, "reason": f"job not sent (status={job.status}) — outcome не применяется"}

    job.outcome = outcome
    if outcome == "WIN" and revenue is not None:
        job.revenue = int(revenue)
    db.commit()

    loss = None
    if outcome == "LOST":
        task = db.query(Task).filter(Task.id == job.task_id).first() if job.task_id else None
        loss = analyze_loss(job, task, db, llm=llm)
    return {"ok": True, "outcome": outcome, "loss": loss}
