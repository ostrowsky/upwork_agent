"""Job qualification — LLM decides APPLY/SKIP per the active task's strategy.

Shared by the worker (auto-qualify NEW jobs) and the UI (re-qualify / override).
Connects are NOT spent here — that happens at submit (Day 7). Qualify only
classifies and routes a job to READY_TO_PROPOSE or SKIPPED.

Status flow: NEW -> READY_TO_PROPOSE | SKIPPED | ERROR
"""
from __future__ import annotations

import json
import re

from database import get_db_session, Job, Task


QUALIFY_SYSTEM = (
    "Ты — СТРОГИЙ квалификатор лидов на Upwork. По стратегии задачи реши, стоит ли "
    "откликаться. Отвечай СТРОГО одним JSON-объектом без markdown, без пояснений:\n"
    '{"decision": "APPLY"|"SKIP", "fit_score": 0-100, "risk_score": 0-100, '
    '"skip_reason": "string|null", "summary": "string"}\n'
    "ЖЁСТКИЕ ПРАВИЛА (нарушение любого → SKIP):\n"
    "1) СТЕК. Основной технологический стек вакансии должен совпадать со стеком "
    "стратегии. Если стратегия про Unity, а вакансия требует другой основной движок/стек "
    "(Unreal, Godot, native iOS/Android, веб-3D — Three.js/Babylon.js/A-Frame/WebGL и т.п.) "
    "— это НЕ наш стек → SKIP, даже если тематика «игры/3D» близка. Смежность темы ≠ совпадение стека.\n"
    "2) БЮДЖЕТ. Если бюджет ЯВНО вне диапазона стратегии — SKIP. Если бюджет НЕ указан, "
    "'Hourly' без ставки, или неясен — НЕ используй бюджет как причину SKIP; оценивай по стеку.\n"
    "3) ИСКЛЮЧЕНИЯ из стратегии (например «без NFT/gambling») соблюдай буквально.\n"
    "Не «натягивай» вакансию на стратегию. fit_score — честное совпадение; risk_score — риск "
    "(мусор, низкий бюджет, расплывчатое ТЗ). Для SKIP всегда дай skip_reason; summary — "
    "1–2 предложения на языке вакансии, явно отметь совпадение/несовпадение стека и бюджета."
)

VALID_DECISIONS = {"APPLY", "SKIP"}


def build_qualify_messages(task: Task | None, job: Job) -> list[dict]:
    strategy = ""
    if task is not None:
        strategy = (task.strategy or task.description or "").strip()
    strategy = strategy or "Без явной стратегии — оценивай по здравому смыслу."

    user = (
        f"СТРАТЕГИЯ ЗАДАЧИ:\n{strategy}\n\n"
        f"ВАКАНСИЯ:\nЗаголовок: {job.title}\n"
        f"Бюджет: {job.budget or 'не указан'}\n"
        f"Описание:\n{job.description}\n\n"
        "Верни JSON по схеме."
    )
    return [
        {"role": "system", "content": QUALIFY_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_qualify_response(text: str) -> dict:
    """Extract and validate the qualify JSON. Raises ValueError if unusable.

    Tolerates ```json fences and surrounding prose by grabbing the first {...}.
    """
    if not text or not text.strip():
        raise ValueError("empty LLM response")

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("no JSON object in response")
        data = json.loads(m.group(0))

    decision = str(data.get("decision", "")).strip().upper()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"invalid decision: {decision!r}")

    def _score(key: str) -> int:
        try:
            return max(0, min(100, int(round(float(data.get(key, 0))))))
        except (TypeError, ValueError):
            return 0

    skip_reason = data.get("skip_reason")
    skip_reason = str(skip_reason).strip() if skip_reason else None
    return {
        "decision": decision,
        "fit_score": _score("fit_score"),
        "risk_score": _score("risk_score"),
        "skip_reason": skip_reason if decision == "SKIP" else None,
        "summary": str(data.get("summary", "")).strip(),
    }


def qualify_job(job: Job, task: Task | None, db, llm=None) -> dict:
    """Qualify one job in place. Retries once on invalid JSON, else status ERROR.

    APPLY -> READY_TO_PROPOSE; SKIP -> SKIPPED. Always logs raw analysis JSON.
    """
    if llm is None:
        from ai import call_llm as llm

    messages = build_qualify_messages(task, job)

    result = None
    for attempt in range(2):
        try:
            raw = llm(messages)
            result = parse_qualify_response(raw)
            break
        except Exception:  # noqa: BLE001 — invalid JSON / LLM error -> retry once
            if attempt == 1:
                job.status = "ERROR"
                db.commit()
                return {"status": "ERROR", "decision": None}

    job.fit_score = result["fit_score"]
    job.risk_score = result["risk_score"]
    job.ai_decision = result["decision"]
    job.ai_summary = result["summary"]
    job.skip_reason = result["skip_reason"]
    job.ai_analysis_json = json.dumps(result, ensure_ascii=False)
    job.status = "READY_TO_PROPOSE" if result["decision"] == "APPLY" else "SKIPPED"
    db.commit()
    return {"status": job.status, "decision": result["decision"]}


def qualify_new_jobs(task_id: int | None, limit: int = 20, db=None, llm=None, progress=None) -> dict:
    """Qualify NEW (and previously ERRORed) jobs. Returns {applied, skipped, errors, total}.

    task_id given -> only that task's NEW/ERROR jobs, using its strategy.
    task_id None  -> all NEW/ERROR jobs, each scored against ITS OWN task's strategy.
    Re-including ERROR lets transient LLM failures self-heal on a later run.
    """
    owns = db is None
    db = db or get_db_session()
    applied = skipped = errors = 0
    task_cache: dict[int | None, Task | None] = {}

    def _task_for(tid):
        if tid not in task_cache:
            task_cache[tid] = db.query(Task).filter(Task.id == tid).first() if tid else None
        return task_cache[tid]

    try:
        # Include ERROR jobs so a transient LLM failure self-heals on a later run
        # (a job that errored twice would otherwise be orphaned forever).
        q = db.query(Job).filter(Job.status.in_(["NEW", "ERROR"]))
        if task_id is not None:
            q = q.filter(Job.task_id == task_id)
            task_cache[task_id] = db.query(Task).filter(Task.id == task_id).first()
        jobs = q.order_by(Job.created_at.asc()).limit(limit).all()
        total = len(jobs)
        print(f"[qualify] start: {total} NEW/ERROR jobs", flush=True)

        for i, job in enumerate(jobs, 1):
            task = _task_for(task_id if task_id is not None else job.task_id)
            res = qualify_job(job, task, db, llm=llm)
            if res["status"] == "READY_TO_PROPOSE":
                applied += 1
            elif res["status"] == "SKIPPED":
                skipped += 1
            else:
                errors += 1
            print(f"[qualify] {i}/{total} #{job.id} -> {res['decision'] or res['status']} "
                  f"({job.title[:45]})", flush=True)
            if progress:
                progress(i, total, f"#{job.id} -> {res['decision'] or res['status']}")
        print(f"[qualify] done: APPLY={applied} SKIP={skipped} ERR={errors}", flush=True)
        return {"applied": applied, "skipped": skipped, "errors": errors, "total": total}
    finally:
        if owns:
            db.close()
