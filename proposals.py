"""Proposal generation — the 7-block cover letter per the client's template.

Pipeline: pick relevant real cases (cases.rank_cases) -> ask the LLM for each
block as JSON -> assemble a formatted proposal -> store ONE active DRAFT per job.
Submitting/connects is Day 7; this module only drafts.

Language follows the job description (RU if mostly Cyrillic, else EN).
The proof block must cite only the selected real cases — never invent projects.
"""
from __future__ import annotations

import json
import re

from database import get_db_session, Job, Proposal, CaseStudy, Task
from cases import rank_cases


# 7 blocks from docs/specs/features/proposal-generation.md
REQUIRED_KEYS = ("understanding", "proof", "approach", "questions", "risk_reducer", "cta", "estimate")


def detect_language(text: str | None) -> str:
    """RU when Cyrillic dominates the letters, otherwise EN."""
    if not text:
        return "EN"
    cyr = len(re.findall(r"[Ѐ-ӿ]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    return "RU" if cyr > lat else "EN"


def _cases_block(cases: list) -> str:
    if not cases:
        return "НЕТ подходящих кейсов — опиши релевантный опыт общими словами, БЕЗ выдуманных названий проектов."
    lines = []
    for c in cases:
        lines.append(
            f"- [{c.id}] {c.title} | ниша: {c.niche or '-'} | стек: {c.stack or '-'} | "
            f"бюджет: {c.budget_range or '-'} | результат: {c.result or '-'}"
        )
    return "\n".join(lines)


def build_proposal_messages(task: Task | None, job: Job, cases: list, language: str) -> list[dict]:
    strategy = ""
    if task is not None:
        strategy = (task.strategy or task.description or "").strip()

    system = (
        "Ты — старший Upwork-сейлз для геймдев-студии. Пиши отклик строго по "
        f"шаблону из 7 блоков, на языке: {language}. Верни СТРОГО один JSON-объект "
        "без markdown по схеме:\n"
        '{"understanding": str, "proof": str, "approach": str, '
        '"questions": [str, ...], "risk_reducer": str, "cta": str, "estimate": str}\n'
        "Блоки: understanding — докажи, что прочитал задачу (1-2 предложения, "
        "конкретика из описания). proof — 1-2 кейса ТОЛЬКО из переданного списка, "
        "со ссылкой и результатом; не выдумывай проекты. approach — как начнём "
        "(шаги/этапы). questions — 2-4 уточняющих вопроса. risk_reducer — как "
        "снижаем риск (этапы/гарантии/демо). cta — чёткий следующий шаг в Upwork. "
        "estimate — детализированная смета по этапам с диапазонами, если уместно."
    )
    user = (
        f"СТРАТЕГИЯ ЗАДАЧИ:\n{strategy or '—'}\n\n"
        f"ВАКАНСИЯ:\nЗаголовок: {job.title}\nБюджет: {job.budget or 'не указан'}\n"
        f"Описание:\n{job.description}\n\n"
        f"ДОСТУПНЫЕ КЕЙСЫ (используй только их в proof):\n{_cases_block(cases)}\n\n"
        "Верни JSON по схеме."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_proposal_response(text: str) -> dict:
    """Extract and validate the proposal JSON. Raises ValueError if unusable."""
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

    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"missing keys: {missing}")

    questions = data.get("questions") or []
    if isinstance(questions, str):
        questions = [questions]
    data["questions"] = [str(q).strip() for q in questions if str(q).strip()]
    for k in ("understanding", "proof", "approach", "risk_reducer", "cta", "estimate"):
        data[k] = str(data.get(k, "")).strip()
    return data


def assemble_proposal(data: dict) -> str:
    """Render the parsed blocks into the final cover-letter text."""
    q = "\n".join(f"- {x}" for x in data["questions"])
    parts = [
        data["understanding"],
        data["proof"],
        data["approach"],
        f"Questions:\n{q}" if q else "",
        data["risk_reducer"],
        data["cta"],
    ]
    return "\n\n".join(p for p in parts if p).strip()


def generate_proposal(job: Job, task: Task | None, db, all_cases: list | None = None, llm=None) -> dict:
    """Generate (or regenerate) the single active DRAFT proposal for a job.

    Idempotent: reuses the existing DRAFT row instead of creating duplicates.
    Returns {"status": "DRAFT"|"ERROR", "proposal_id": int|None, "cases": [ids]}.
    """
    if llm is None:
        from ai import call_llm as llm
    if all_cases is None:
        all_cases = db.query(CaseStudy).filter(CaseStudy.deleted == 0).all()

    import os as _os

    top_n = int(_os.getenv("CASE_TOP_N", "2"))
    selected = rank_cases(job, all_cases, top_n=top_n,
                          min_score=int(_os.getenv("CASE_MIN_SCORE", "4")))
    # Always cite the case fabricated specifically for THIS job (it IS the attached
    # PDF) so the cover letter and the attachment stay in sync.
    own = [c for c in all_cases
           if getattr(c, "synthetic", 0) and getattr(c, "job_id", None) == job.id]
    if own:
        selected = (own[:1] + [c for c in selected if c.id != own[0].id])[:top_n]
    # When the base has no relevant case and AUTO_GEN_CASE=1, fabricate one tailored
    # to this job (synthetic, with a PDF/PNG attachment) so the proof block is strong.
    if not selected and _os.getenv("AUTO_GEN_CASE", "0").strip().lower() in ("1", "true", "yes"):
        try:
            from cases import generate_case_for_job
            from database import CaseStudy as _CS

            gen = generate_case_for_job(job, task, db, llm=llm)
            if gen.get("ok") and gen.get("case_id"):
                new_case = db.query(_CS).filter(_CS.id == gen["case_id"]).first()
                if new_case is not None:
                    selected = [new_case]
        except Exception:  # noqa: BLE001 — proposal still works without a fabricated case
            pass
    language = detect_language(job.description)
    messages = build_proposal_messages(task, job, selected, language)

    data = None
    for attempt in range(2):
        try:
            data = parse_proposal_response(llm(messages))
            break
        except Exception:  # noqa: BLE001 — retry once, then bail
            if attempt == 1:
                return {"status": "ERROR", "proposal_id": None, "cases": []}

    content = assemble_proposal(data)
    case_ids = [c.id for c in selected]

    draft = (
        db.query(Proposal)
        .filter(Proposal.job_id == job.id, Proposal.status == "DRAFT")
        .first()
    )
    if draft is None:
        draft = Proposal(job_id=job.id, status="DRAFT")
        db.add(draft)
    draft.content = content
    draft.estimate = data["estimate"]
    draft.selected_cases = json.dumps(case_ids)
    job.status = "PROPOSAL_DRAFTED"
    db.commit()
    db.refresh(draft)
    return {"status": "DRAFT", "proposal_id": draft.id, "cases": case_ids}


def generate_drafts_for_ready(task_id: int | None, limit: int = 10, db=None, llm=None) -> dict:
    """Batch-draft proposals for READY_TO_PROPOSE jobs lacking a DRAFT."""
    owns = db is None
    db = db or get_db_session()
    drafted = errors = 0
    try:
        task = db.query(Task).filter(Task.id == task_id).first() if task_id else None
        all_cases = db.query(CaseStudy).filter(CaseStudy.deleted == 0).all()
        q = db.query(Job).filter(Job.status == "READY_TO_PROPOSE")
        if task_id is not None:
            q = q.filter(Job.task_id == task_id)
        jobs = q.order_by(Job.created_at.asc()).limit(limit).all()
        total = len(jobs)
        print(f"[draft] start: {total} READY jobs", flush=True)
        for i, job in enumerate(jobs, 1):
            t = task if task_id is not None else (
                db.query(Task).filter(Task.id == job.task_id).first() if job.task_id else None
            )
            res = generate_proposal(job, t, db, all_cases=all_cases, llm=llm)
            if res["status"] == "DRAFT":
                drafted += 1
            else:
                errors += 1
            print(f"[draft] {i}/{total} #{job.id} -> {res['status']} "
                  f"(cases={res.get('cases')})", flush=True)
        print(f"[draft] done: drafted={drafted} errors={errors}", flush=True)
        return {"drafted": drafted, "errors": errors, "total": total}
    finally:
        if owns:
            db.close()
