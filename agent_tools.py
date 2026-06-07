"""Agent chat tools — let the «Чат с агентом» agent DO things, not just advise.

Only SAFE actions are exposed here: LLM/DB work that has no browser phase and
nothing irreversible/outward-facing. Submitting proposals and sending client
messages stay gated behind their explicit flags / manual UI — they are NOT tools.

`select_action` (one LLM call) returns either a plain reply or a tool call;
`run_action` executes it. Both are isolated so the parsing is unit-testable.
"""
from __future__ import annotations

import json
import re

# name -> (human description, arg names)
ACTION_SPECS = [
    ("qualify_new", "Квалифицировать новые вакансии активной задачи (APPLY/SKIP)", []),
    ("generate_drafts", "Сгенерировать черновики откликов для готовых вакансий", []),
    ("draft_client_reply", "Сгенерировать черновик ответа клиенту", ["client"]),
    ("generate_case", "Сгенерировать кейс-вложение под вакансию", ["job_id"]),
    ("send_report", "Сформировать и отправить дневной отчёт", []),
    ("import_jobs", "Импортировать вакансии из ленты Upwork (браузер)", []),
    ("search_jobs", "Найти вакансии по ключевым словам и импортировать (браузер)", ["query"]),
    ("import_messages", "Импортировать переписку (инбокс) с Upwork (браузер)", []),
]
ACTION_NAMES = {a[0] for a in ACTION_SPECS}


def actions_help() -> str:
    return "\n".join(f"- {name}({', '.join(args)}): {desc}" for name, desc, args in ACTION_SPECS)


def parse_action(text: str) -> dict:
    """Parse the selector LLM output into {action, args, reply}. Robust to fences/prose."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", raw).strip()
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        # No structured action → treat the whole text as a plain reply.
        return {"action": "none", "args": {}, "reply": raw}
    action = str(data.get("action", "none") or "none").strip()
    if action not in ACTION_NAMES:
        action = "none"
    args = data.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    return {"action": action, "args": args, "reply": str(data.get("reply", "") or "").strip()}


def select_action(user_msg: str, history, context: str, llm) -> dict:
    """One LLM call: decide on a tool call or a plain reply. Returns parse_action()."""
    system = (
        "Ты — AI-агент по продажам на Upwork с доступом к ИНСТРУМЕНТАМ. "
        "По сообщению оператора реши: вызвать инструмент или просто ответить.\n"
        "ИНСТРУМЕНТЫ:\n" + actions_help() + "\n\n"
        "Верни СТРОГО JSON без markdown: "
        '{"action": "<имя инструмента или none>", "args": {<аргументы>}, "reply": "<текст оператору>"}. '
        "Вызывай инструмент ТОЛЬКО если оператор явно просит действие. Иначе action=none и дай ответ в reply, "
        "опираясь на контекст. Отвечай на языке оператора.\n\n=== КОНТЕКСТ ===\n" + context
    )
    messages = [{"role": "system", "content": system}]
    for role, content in (history or [])[-10:]:
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_msg})
    return parse_action(llm(messages))


# --------------------------------------------------------------------------
# Action implementations (safe: no browser, nothing irreversible/outward-facing)
# --------------------------------------------------------------------------

def _run_browser(fn):
    """Run a browser op under the cross-process lock (so it's safe even while the
    worker runs). Self-heal of a wedged Edge profile happens inside browser_page().

    Returns ("busy", None) if another process holds the lock, else ("ok", result).
    """
    from browser_lock import is_busy, acquire, release

    if is_busy():
        return "busy", None
    acquire("agent-chat")
    try:
        return "ok", fn()
    finally:
        release()


def _resolve_client(db, ref):
    from database import Client

    if ref is None:
        return None
    s = str(ref).strip()
    if s.isdigit():
        c = db.query(Client).filter(Client.id == int(s)).first()
        if c:
            return c
    return db.query(Client).filter(Client.name.ilike(f"%{s}%")).first()


def run_action(name: str, args: dict, task_id, db=None) -> dict:
    """Execute a tool. Returns {ok, summary}. Never raises (errors → ok=False)."""
    from database import get_db_session

    owns = db is None
    db = db or get_db_session()
    try:
        if name == "qualify_new":
            from qualify import qualify_new_jobs

            r = qualify_new_jobs(task_id, db=db)
            return {"ok": True, "summary": f"Квалификация: APPLY {r['applied']}, SKIP {r['skipped']}, "
                                           f"ошибок {r['errors']} (из {r['total']})."}
        if name == "generate_drafts":
            from proposals import generate_drafts_for_ready

            r = generate_drafts_for_ready(task_id, db=db)
            return {"ok": True, "summary": f"Черновики откликов: создано {r['drafted']}, "
                                           f"ошибок {r['errors']} (из {r['total']}). См. «Вакансии»."}
        if name == "draft_client_reply":
            import clients as C

            client = _resolve_client(db, args.get("client"))
            if client is None:
                return {"ok": False, "summary": f"Клиент не найден: {args.get('client')!r}."}
            text = C.generate_draft_text(client, db)
            msgs = C.get_messages(db, client.id)
            if msgs and msgs[-1].direction == "inbound":
                msgs[-1].ai_draft = text
                db.commit()
            return {"ok": True, "summary": f"Черновик ответа для «{client.name or client.id}» готов "
                                           f"(см. «Клиенты»):\n{text[:400]}"}
        if name == "generate_case":
            from cases import generate_case_for_job
            from database import Job, Task

            job = db.query(Job).filter(Job.id == int(args.get("job_id"))).first() if args.get("job_id") else None
            if job is None:
                return {"ok": False, "summary": f"Вакансия не найдена: job_id={args.get('job_id')!r}."}
            task = db.query(Task).filter(Task.id == job.task_id).first() if job.task_id else None
            res = generate_case_for_job(job, task, db)
            if res.get("ok"):
                return {"ok": True, "summary": f"Кейс под вакансию #{job.id} готов: «{res['title']}» "
                                               f"(PDF в «Кейсы»)."}
            return {"ok": False, "summary": f"Не удалось сгенерировать кейс: {res.get('reason')}."}
        if name == "send_report":
            from reporting import send_report

            r = send_report(task_id=task_id, db=db)
            return {"ok": True, "summary": f"Отчёт сформирован. Доставка: telegram={r['telegram']}, "
                                           f"discord={r['discord']}."}

        # --- Browser actions (cross-process lock + self-heal) ---
        if name == "import_jobs":
            if task_id is None:
                return {"ok": False, "summary": "Нет активной задачи — выбери её в «Компании и задачи»."}
            from jobs import ingest_from_upwork

            st_, r = _run_browser(lambda: ingest_from_upwork(task_id))
            if st_ == "busy":
                return {"ok": False, "summary": "Браузер занят (worker/др. операция). Повтори позже."}
            if r.get("ok"):
                return {"ok": True, "summary": f"Импорт ленты: добавлено {r['added']}, дублей "
                                               f"{r['skipped_dup']} ({r.get('reason', '')})."}
            return {"ok": False, "summary": f"Не удалось импортировать ленту: {r.get('reason')}."}
        if name == "search_jobs":
            if task_id is None:
                return {"ok": False, "summary": "Нет активной задачи — выбери её в «Компании и задачи»."}
            query = str(args.get("query") or "").strip()
            if not query:
                return {"ok": False, "summary": "Не указан поисковый запрос (query)."}
            from jobs import ingest_search

            st_, r = _run_browser(lambda: ingest_search(query, task_id))
            if st_ == "busy":
                return {"ok": False, "summary": "Браузер занят (worker/др. операция). Повтори позже."}
            if r.get("ok"):
                return {"ok": True, "summary": f"Поиск «{query}»: добавлено {r['added']}, дублей "
                                               f"{r['skipped_dup']} ({r.get('reason', '')})."}
            return {"ok": False, "summary": f"Поиск не удался: {r.get('reason')}."}
        if name == "import_messages":
            from messages import import_messages as _imp

            st_, r = _run_browser(lambda: _imp(max_rooms=int(args.get("count") or 10)))
            if st_ == "busy":
                return {"ok": False, "summary": "Браузер занят (worker/др. операция). Повтори позже."}
            if r.get("ok"):
                return {"ok": True, "summary": f"Инбокс: диалогов {r.get('rooms', 0)}, новых сообщений "
                                               f"{r.get('imported', 0)} (см. «Клиенты»)."}
            return {"ok": False, "summary": f"Не удалось импортировать инбокс: {r.get('reason')}."}

        return {"ok": False, "summary": f"Неизвестный инструмент: {name}."}
    except Exception as e:  # noqa: BLE001 — surface the error to the operator, don't crash the chat
        return {"ok": False, "summary": f"Ошибка инструмента {name}: {type(e).__name__}: {e}"}
    finally:
        if owns:
            db.close()
