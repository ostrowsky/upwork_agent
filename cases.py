"""Case library — selection of relevant cases for a job (proof for proposals).

`rank_cases` is a pure, deterministic scorer (no LLM) so it is unit-testable and
cheap: the proposal step (Day 6) calls it to pick which real case studies to cite.
Cases are a global portfolio library; only records from the DB are ever used.
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-zA-Z0-9#+.]+")
# Generic words that shouldn't drive relevance.
_STOPWORDS = {
    "the", "and", "for", "with", "you", "our", "are", "this", "that", "will",
    "have", "need", "looking", "game", "games", "app", "developer", "development",
    "experience", "project", "work", "build", "using", "want", "able",
}


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {
        t.lower()
        for t in _TOKEN_RE.findall(text)
        if len(t) > 2 and t.lower() not in _STOPWORDS
    }


def score_case(job_text_tokens: set[str], case) -> int:
    """Relevance of one case to a job's token set.

    niche/stack/title matches are weighted higher than description matches.
    """
    score = 0
    for field, weight in (
        (case.niche, 3),
        (case.stack, 3),
        (case.title, 2),
        (case.description, 1),
    ):
        overlap = _tokens(field) & job_text_tokens
        score += weight * len(overlap)
    return score


def rank_cases(job, cases, top_n: int = 2, min_score: int = 1) -> list:
    """Return up to ``top_n`` cases with score ≥ ``min_score``.

    Deterministic: ties break by case.id ascending. Raising ``min_score`` avoids
    citing weakly-relevant cases (e.g. a shooter case on a 2D-art job); when
    nothing clears the bar, returns [] and the proposal omits the proof block.
    """
    job_tokens = _tokens(f"{getattr(job, 'title', '')} {getattr(job, 'description', '')}")
    scored = [(score_case(job_tokens, c), c) for c in cases]
    scored = [(s, c) for s, c in scored if s >= max(1, min_score)]
    scored.sort(key=lambda sc: (-sc[0], getattr(sc[1], "id", 0)))
    return [c for _, c in scored[:top_n]]


# --------------------------------------------------------------------------
# Synthetic, job-tailored case generation (LLM) + attachment artifact.
# Owner-approved fabrication (see docs/specs/product-map.md); rows are flagged
# synthetic=1 so they're distinguishable from the manual portfolio.
# --------------------------------------------------------------------------

CASE_GEN_SYSTEM = (
    "Ты — сильный pre-sales инженер геймдев-студии. По описанию вакансии на Upwork "
    "придумай кейс из «портфолио» студии, который убедит заказчика выбрать нас. "
    "Кейс правдоподобный, конкретный, с измеримыми результатами.\n"
    "ВАЖНО — кейс НЕ должен быть копией вакансии: возьми ТУ ЖЕ предметную область "
    "и ПОХОЖУЮ инженерную задачу, но ДРУГОЕ решение и другой продукт. Заказчик должен "
    "увидеть, что мы уже решали задачу такого класса, а не что мы пересказали его ТЗ.\n"
    "Верни СТРОГО один JSON-объект без markdown:\n"
    '{"title": str, "niche": str, "stack": str, "budget_range": str, "duration": str, '
    '"role": str, "summary": str, "narrative": str, "approach": [str, ...], '
    '"results": [str, ...], "metrics": [{"label": str, "value": str}, ...]}\n'
    "title — короткое название проекта. stack — список технологий через запятую. "
    "budget_range — диапазон в $. metrics — 3-4 коротких KPI (value <=14 симв.). "
    "approach — 2-4 пункта. results — 2-4 измеримых результата.\n"
    "summary — 1-2 предложения для списков.\n"
    "narrative — 3-4 абзаца связного текста (600-1200 символов): контекст и бизнес-задача "
    "клиента, в чём была техническая сложность, какое решение выбрали и ПОЧЕМУ именно его "
    "(с альтернативами, которые отвергли), что получилось в цифрах. Без маркированных "
    "списков и заголовков — обычная проза. Явно видно, что задача СМЕЖНАЯ с вакансией, "
    "а не та же самая.\n"
    "Пиши на языке вакансии (EN/RU)."
)


def _parse_case_json(text: str) -> dict:
    import json
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("case JSON not found")
        data = json.loads(m.group(0))
    if not isinstance(data, dict) or not str(data.get("title", "")).strip():
        raise ValueError("case JSON missing title")
    return data


def build_case_gen_messages(job, task) -> list[dict]:
    strategy = ((getattr(task, "strategy", None) or getattr(task, "description", None)) if task else "") or "—"
    user = (
        f"СТРАТЕГИЯ СТУДИИ:\n{strategy}\n\n"
        f"ВАКАНСИЯ:\n{getattr(job, 'title', '')}\n"
        f"Бюджет: {getattr(job, 'budget', None) or '—'}\n"
        f"{(getattr(job, 'description', '') or '')[:1500]}\n\nВерни JSON-кейс."
    )
    return [{"role": "system", "content": CASE_GEN_SYSTEM}, {"role": "user", "content": user}]


def _as_text(value, limit: int | None = None) -> str | None:
    """Coerce an LLM field that must be scalar text into a string.

    The model does not always honour the requested shape: `stack` has come back
    as ["Unity", "Photon", "C#"] instead of a string, and SQLite cannot bind a
    list, so the INSERT raised ProgrammingError and took the whole UI page down.
    Lists are flattened to a comma-joined string; empty/blank becomes None.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(x).strip() for x in value if str(x).strip())
    elif isinstance(value, dict):
        value = ", ".join(f"{k}: {v}" for k, v in value.items())
    else:
        value = str(value)
    value = value.strip()
    if not value:
        return None
    return value[:limit] if limit else value


def generate_case_for_job(job, task, db, llm=None, render: bool = True) -> dict:
    """Fabricate a job-tailored case, save it (synthetic=1), render a PDF+PNG attachment.

    Returns {"ok", "case_id", "artifact_path", "png_path", "title", "reason"}.
    """
    from database import CaseStudy

    if llm is None:
        from ai import call_llm as llm
    messages = build_case_gen_messages(job, task)
    data = None
    last_err = None
    for _ in range(3):  # transient LLM non-JSON → retry (same pattern as qualify)
        try:
            data = _parse_case_json(llm(messages))
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if data is None:
        return {"ok": False, "case_id": None, "artifact_path": None, "png_path": None,
                "title": None, "reason": f"generation failed: {last_err}"}

    results = [str(x) for x in (data.get("results") or []) if str(x).strip()]
    approach = [str(x) for x in (data.get("approach") or []) if str(x).strip()]
    metrics = [m for m in (data.get("metrics") or []) if isinstance(m, dict) and str(m.get("value", "")).strip()]
    summary = str(data.get("summary") or "").strip()
    # Quality gate: a substanceless case (no results, no metrics, thin summary) is
    # worse than none — reject so the caller falls back to a real ranked case.
    if len(results) < 1 or len(metrics) < 1 or len(summary) < 40:
        return {"ok": False, "case_id": None, "artifact_path": None, "png_path": None,
                "title": None, "reason": "case too thin (no results/metrics) — use an existing case"}
    case = CaseStudy(
        # Every scalar text field goes through _as_text: the model returns a
        # list here often enough that an uncoerced value crashes the insert.
        title=_as_text(data.get("title"), 255) or "Untitled case",
        niche=_as_text(data.get("niche"), 255),
        stack=_as_text(data.get("stack")),
        description=_as_text(data.get("summary")) or _as_text(data.get("title")) or "",
        narrative=_as_text(data.get("narrative")),
        result="\n".join(results) or None,
        budget_range=_as_text(data.get("budget_range"), 100),
        synthetic=1,
        job_id=getattr(job, "id", None),
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    artifact_path = png_path = None
    if render:
        layout = {
            "id": case.id, "title": case.title, "niche": case.niche,
            "budget_range": case.budget_range, "duration": data.get("duration"),
            "role": data.get("role"), "stack": case.stack, "description": case.description,
            "approach_list": [str(x) for x in (data.get("approach") or []) if str(x).strip()],
            "results_list": results,
            "metrics_list": [m for m in (data.get("metrics") or []) if isinstance(m, dict)],
        }
        try:
            from case_artifacts import render_case_pdf, render_case_png

            artifact_path = render_case_pdf(layout)
            png_path = render_case_png(layout)
            case.artifact_path = artifact_path
            db.commit()
        except Exception as e:  # noqa: BLE001 — case row is still usable without the file
            return {"ok": True, "case_id": case.id, "artifact_path": None, "png_path": None,
                    "title": case.title, "reason": f"saved but render failed: {e}"}

    return {"ok": True, "case_id": case.id, "artifact_path": artifact_path,
            "png_path": png_path, "title": case.title, "reason": "ok"}
