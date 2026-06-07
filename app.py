import streamlit as st
import pandas as pd

from database import (
    init_db,
    get_db_session,
    Job,
    CaseStudy,
    Company,
    Task,
    TaskMessage,
)


st.set_page_config(
    page_title="Upwork AI Sales Assistant",
    page_icon="🤖",
    layout="wide",
)


init_db()


def make_progress(prefix: str):
    """Return (bar, callback) for a determinate progress bar over a batch.

    Pass `callback` as `progress=` to a batch function; call `bar.empty()` after.
    """
    bar = st.progress(0.0, text=f"{prefix}…")

    def cb(done, total, label=""):
        frac = (done / total) if total else 1.0
        bar.progress(min(frac, 1.0), text=f"{prefix} {done}/{total} · {label}")

    return bar, cb


def paginate(items, key, default_size=10, size_options=(5, 10, 20, 50)):
    """Render pagination controls and return the slice for the current page.

    Per-list page/size are kept in session_state under the given `key`.
    """
    total = len(items)
    sk_page, sk_size = f"pg_{key}", f"pgsize_{key}"
    c0, c1, c2, c3 = st.columns([2, 1, 1, 2])
    with c0:
        size = st.selectbox(
            "На странице", list(size_options),
            index=list(size_options).index(default_size) if default_size in size_options else 0,
            key=sk_size,
        )
    pages = max(1, (total + size - 1) // size)
    cur = min(max(1, st.session_state.get(sk_page, 1)), pages)
    with c1:
        if st.button("◀ Назад", key=f"prev_{key}", disabled=cur <= 1, use_container_width=True):
            st.session_state[sk_page] = cur - 1
            st.rerun()
    with c2:
        if st.button("Вперёд ▶", key=f"next_{key}", disabled=cur >= pages, use_container_width=True):
            st.session_state[sk_page] = cur + 1
            st.rerun()
    with c3:
        st.caption(f"Страница {cur} из {pages} · всего {total}")
    start = (cur - 1) * size
    return items[start:start + size]


def run_browser_op(fn):
    """Run a browser operation under the cross-process lock (gap #3).

    Returns (ok, result). ok=False with result=None if the browser is busy
    (worker or another action holds the Edge profile).
    """
    from browser_lock import is_busy, acquire, release

    if is_busy():
        return False, None
    acquire("ui")
    try:
        return True, fn()
    finally:
        release()


def edit_delete_controls(key, content, on_save, on_delete, label="Текст", height=100):
    """Inline ✏️/🗑 controls for a single stored text value."""
    with st.expander("✏️ Редактировать / удалить", expanded=False):
        new = st.text_area(label, value=content or "", height=height, key=f"edit_{key}")
        c1, c2 = st.columns(2)
        if c1.button("💾 Сохранить", key=f"save_{key}"):
            on_save(new.strip())
            st.rerun()
        if c2.button("🗑 Удалить", key=f"del_{key}"):
            on_delete()
            st.rerun()


def get_jobs():
    db = get_db_session()
    try:
        return db.query(Job).order_by(Job.created_at.desc()).all()
    finally:
        db.close()


def get_case_studies():
    db = get_db_session()
    try:
        return (
            db.query(CaseStudy)
            .filter(CaseStudy.deleted == 0)
            .order_by(CaseStudy.created_at.desc())
            .all()
        )
    finally:
        db.close()


def update_case_study(case_id, **fields):
    db = get_db_session()
    try:
        case = db.query(CaseStudy).filter(CaseStudy.id == case_id).first()
        if case:
            for k, v in fields.items():
                setattr(case, k, (v or None) if k != "description" else v)
        db.commit()
    finally:
        db.close()


def delete_case_study(case_id):
    """Soft-delete if cited by a proposal (spec invariant), else hard-delete."""
    from database import Proposal

    db = get_db_session()
    try:
        cited = (
            db.query(Proposal)
            .filter(Proposal.selected_cases.like(f"%{case_id}%"))
            .first()
        )
        case = db.query(CaseStudy).filter(CaseStudy.id == case_id).first()
        if not case:
            return "missing"
        if cited:
            case.deleted = 1
            db.commit()
            return "soft"
        db.delete(case)
        db.commit()
        return "hard"
    finally:
        db.close()


def override_job_decision(job_id, decision):
    """Operator manually overrides the AI decision (spec: manual override)."""
    db = get_db_session()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.ai_decision = decision
            job.status = "READY_TO_PROPOSE" if decision == "APPLY" else "SKIPPED"
            db.commit()
    finally:
        db.close()


def get_draft_proposal(job_id):
    from database import Proposal

    db = get_db_session()
    try:
        return (
            db.query(Proposal)
            .filter(Proposal.job_id == job_id, Proposal.status == "DRAFT")
            .first()
        )
    finally:
        db.close()


def get_cases_for_job(job_id):
    """Synthetic cases generated for a specific job (newest first)."""
    db = get_db_session()
    try:
        return (
            db.query(CaseStudy)
            .filter(CaseStudy.job_id == job_id, CaseStudy.deleted == 0)
            .order_by(CaseStudy.created_at.desc())
            .all()
        )
    finally:
        db.close()


def generate_case_for_job_ui(job_id):
    """UI trigger: fabricate a job-tailored synthetic case + PDF/PNG attachment (LLM only)."""
    from cases import generate_case_for_job
    from database import Task

    db = get_db_session()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return {"ok": False, "reason": "job missing"}
        task = db.query(Task).filter(Task.id == job.task_id).first() if job.task_id else None
        return generate_case_for_job(job, task, db)
    finally:
        db.close()


def generate_proposal_for_job(job_id):
    """UI trigger: draft a proposal for one job (LLM only)."""
    from proposals import generate_proposal
    from database import Task

    db = get_db_session()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            return {"status": "ERROR", "proposal_id": None, "cases": []}
        task = db.query(Task).filter(Task.id == job.task_id).first() if job.task_id else None
        return generate_proposal(job, task, db)
    finally:
        db.close()


def update_proposal_content(proposal_id, content, estimate):
    from database import Proposal

    db = get_db_session()
    try:
        p = db.query(Proposal).filter(Proposal.id == proposal_id).first()
        if p:
            p.content = content
            p.estimate = estimate or None
        db.commit()
    finally:
        db.close()


def submit_proposal_for_job(job_id):
    """UI trigger: submit the job's draft (dry-run unless AUTO_SUBMIT=1)."""
    from submit import submit_proposal
    from database import Proposal

    db = get_db_session()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        proposal = (
            db.query(Proposal)
            .filter(Proposal.job_id == job_id, Proposal.status == "DRAFT")
            .first()
        )
        if not job:
            return {"ok": False, "submitted": False, "reason": "job missing"}
        return submit_proposal(job, proposal, db)
    finally:
        db.close()


def create_job(title, description, budget, source_url):
    """Manual import → shared ingest (dedup-aware). Attributes to active task."""
    from jobs import ingest_jobs

    active = get_active_task()
    return ingest_jobs(
        [
            {
                "title": title,
                "description": description,
                "budget": budget,
                "source_url": source_url,
            }
        ],
        task_id=active.id if active else None,
    )


def create_case_study(title, niche, stack, description, result, budget_range, url):
    db = get_db_session()
    try:
        case = CaseStudy(
            title=title,
            niche=niche or None,
            stack=stack or None,
            description=description,
            result=result or None,
            budget_range=budget_range or None,
            url=url or None,
        )
        db.add(case)
        db.commit()
        db.refresh(case)
        return case
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Companies & Tasks (Day 1)
# ---------------------------------------------------------------------------

CHAT_HISTORY_WINDOW = 12  # last N messages fed to the LLM


def get_companies():
    db = get_db_session()
    try:
        return db.query(Company).order_by(Company.created_at.desc()).all()
    finally:
        db.close()


def create_company(name, upwork_email, notes):
    db = get_db_session()
    try:
        company = Company(
            name=name,
            upwork_email=upwork_email or None,
            notes=notes or None,
        )
        db.add(company)
        db.commit()
        db.refresh(company)
        return company
    finally:
        db.close()


def get_tasks(company_id=None):
    db = get_db_session()
    try:
        q = db.query(Task)
        if company_id is not None:
            q = q.filter(Task.company_id == company_id)
        return q.order_by(Task.created_at.desc()).all()
    finally:
        db.close()


def create_task(company_id, name, description):
    db = get_db_session()
    try:
        task = Task(
            company_id=company_id,
            name=name,
            description=description or None,
            strategy=description or None,
            is_active=0,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task
    finally:
        db.close()


# --- Generic edit/delete helpers for stored text entities ---

def update_row(model, row_id, **fields):
    db = get_db_session()
    try:
        row = db.query(model).filter(model.id == row_id).first()
        if row:
            for k, v in fields.items():
                setattr(row, k, v)
            db.commit()
    finally:
        db.close()


def delete_row(model, row_id):
    db = get_db_session()
    try:
        row = db.query(model).filter(model.id == row_id).first()
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()


def delete_task_cascade(task_id):
    """Delete a task and its chat messages (jobs keep their task_id → orphaned)."""
    db = get_db_session()
    try:
        db.query(TaskMessage).filter(TaskMessage.task_id == task_id).delete()
        db.query(Task).filter(Task.id == task_id).delete()
        db.commit()
    finally:
        db.close()


def set_active_task(task_id):
    """At most one task may be active for the worker at a time."""
    db = get_db_session()
    try:
        db.query(Task).update({Task.is_active: 0})
        db.query(Task).filter(Task.id == task_id).update({Task.is_active: 1})
        db.commit()
    finally:
        db.close()


def get_active_task():
    db = get_db_session()
    try:
        return db.query(Task).filter(Task.is_active == 1).first()
    finally:
        db.close()


def get_task_messages(task_id):
    db = get_db_session()
    try:
        return (
            db.query(TaskMessage)
            .filter(TaskMessage.task_id == task_id)
            .order_by(TaskMessage.created_at.asc(), TaskMessage.id.asc())
            .all()
        )
    finally:
        db.close()


def add_task_message(task_id, role, content):
    db = get_db_session()
    try:
        msg = TaskMessage(task_id=task_id, role=role, content=content)
        db.add(msg)
        db.commit()
        db.refresh(msg)
        return msg
    finally:
        db.close()


def build_task_system_prompt(task):
    """System prompt = накопленная стратегия задачи (инвариант спеки)."""
    strategy = (task.strategy or task.description or "").strip()
    base = (
        "Ты — стратег по продажам на Upwork. Помогаешь оператору уточнить "
        "стратегию задачи: ниши, бюджеты, фильтры вакансий, позиционирование. "
        "Отвечай кратко и по делу, на языке оператора (EN или RU)."
    )
    if strategy:
        base += f"\n\nТекущая стратегия задачи «{task.name}»:\n{strategy}"
    return base


def task_chat_reply(task):
    """Build messages from stored history and call the LLM. Lazy-imports ai."""
    from ai import call_llm

    history = get_task_messages(task.id)
    messages = [{"role": "system", "content": build_task_system_prompt(task)}]
    for m in history[-CHAT_HISTORY_WINDOW:]:
        messages.append({"role": m.role, "content": m.content})
    return call_llm(messages)


def render_companies_tasks():
    st.title("Компании и задачи")

    with st.expander("Добавить компанию", expanded=False):
        with st.form("create_company_form"):
            name = st.text_input("Название компании")
            upwork_email = st.text_input("Upwork email (учётка)")
            notes = st.text_area("Заметки", height=80)
            if st.form_submit_button("Сохранить компанию"):
                if not name.strip():
                    st.error("Нужно указать название компании.")
                else:
                    create_company(name.strip(), upwork_email.strip(), notes.strip())
                    st.success("Компания сохранена.")
                    st.rerun()

    companies = get_companies()
    if not companies:
        st.info("Пока нет компаний. Добавьте первую выше.")
        return

    company_map = {f"#{c.id} — {c.name}": c for c in companies}
    selected_company = company_map[st.selectbox("Компания", list(company_map.keys()))]

    with st.expander("✏️ Редактировать / удалить компанию"):
        with st.form(f"edit_company_{selected_company.id}"):
            ec_name = st.text_input("Название", value=selected_company.name or "")
            ec_email = st.text_input("Upwork email", value=selected_company.upwork_email or "")
            ec_notes = st.text_area("Заметки", value=selected_company.notes or "", height=70)
            if st.form_submit_button("💾 Сохранить компанию"):
                update_row(Company, selected_company.id, name=ec_name.strip() or None,
                           upwork_email=ec_email.strip() or None, notes=ec_notes.strip() or None)
                st.success("Сохранено.")
                st.rerun()
        if st.button("🗑 Удалить компанию", key=f"delcomp_{selected_company.id}"):
            delete_row(Company, selected_company.id)
            st.success("Компания удалена.")
            st.rerun()

    with st.expander("Добавить задачу", expanded=False):
        with st.form("create_task_form"):
            t_name = st.text_input("Название задачи")
            t_desc = st.text_area(
                "Описание / стратегия",
                height=120,
                placeholder="Например: Unity игры, $5k–$20k, без NFT",
            )
            if st.form_submit_button("Сохранить задачу"):
                if not t_name.strip():
                    st.error("Нужно указать название задачи.")
                else:
                    create_task(selected_company.id, t_name.strip(), t_desc.strip())
                    st.success("Задача создана.")
                    st.rerun()

    st.divider()

    tasks = get_tasks(selected_company.id)
    if not tasks:
        st.info("У компании пока нет задач.")
        return

    active = get_active_task()
    task_map = {
        f"{'⭐ ' if t.is_active else ''}#{t.id} — {t.name}": t for t in tasks
    }
    selected_task = task_map[st.selectbox("Задача", list(task_map.keys()))]

    col_a, col_b = st.columns([1, 3])
    with col_a:
        is_active = bool(selected_task.is_active)
        if is_active:
            st.success("Активна для worker")
        elif st.button("Сделать активной"):
            set_active_task(selected_task.id)
            st.rerun()
    with col_b:
        if active:
            st.caption(f"Сейчас активна: #{active.id} — {active.name}")
        else:
            st.caption("Активной задачи нет.")

    with st.expander("✏️ Редактировать / удалить задачу"):
        with st.form(f"edit_task_{selected_task.id}"):
            et_name = st.text_input("Название", value=selected_task.name or "")
            et_strategy = st.text_area("Стратегия", value=selected_task.strategy or "", height=120)
            if st.form_submit_button("💾 Сохранить задачу"):
                update_row(Task, selected_task.id, name=et_name.strip() or None,
                           strategy=et_strategy.strip() or None)
                st.success("Сохранено.")
                st.rerun()
        if st.button("🗑 Удалить задачу (с чатом)", key=f"deltask_{selected_task.id}"):
            delete_task_cascade(selected_task.id)
            st.success("Задача удалена.")
            st.rerun()

    st.subheader("Чат-стратегия задачи")

    for m in get_task_messages(selected_task.id):
        with st.chat_message(m.role):
            st.write(m.content)
            edit_delete_controls(
                f"tm_{m.id}", m.content,
                on_save=lambda v, mid=m.id: update_row(TaskMessage, mid, content=v),
                on_delete=lambda mid=m.id: delete_row(TaskMessage, mid),
                label="Сообщение",
            )

    user_input = st.chat_input("Уточните стратегию задачи…")
    if user_input:
        add_task_message(selected_task.id, "user", user_input)
        try:
            reply = task_chat_reply(selected_task)
        except Exception as exc:  # noqa: BLE001 — surface LLM/config errors in UI
            reply = f"[Ошибка LLM: {exc}]"
        add_task_message(selected_task.id, "assistant", reply)
        st.rerun()


def render_dashboard():
    st.title("Upwork AI Sales Assistant")

    jobs = get_jobs()
    cases = get_case_studies()

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Вакансий", len(jobs))

    with col2:
        st.metric("Кейсов", len(cases))

    with col3:
        proposal_ready = len(
            [
                j
                for j in jobs
                if j.status in ["PROPOSAL_DRAFTED", "APPROVED", "SENT_MANUALLY"]
            ]
        )
        st.metric("С откликами", proposal_ready)

    with col4:
        skipped = len([j for j in jobs if j.status == "SKIPPED"])
        st.metric("Пропущено", skipped)

    st.divider()

    # Funnel metrics (ТЗ: spent connects / replies / interviews / hires / revenue).
    from analytics import dashboard_metrics

    data = dashboard_metrics()
    f = data["funnel"]
    st.subheader("Воронка")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    from connects import read_balance as _read_balance

    _bal = _read_balance()
    m1.metric("Откликов", f["proposals_sent"])
    m2.metric("Connects потрачено", f["connects_spent"],
              help=(f"Баланс: {_bal['balance']} (на {_bal['updated_at'][:16]})" if _bal else None))
    m3.metric("Ответов", f["replies"])
    m4.metric("Интервью", f["interviews"])
    m5.metric("Наймов", f["hires"])
    m6.metric("Выручка", f"${f['revenue']:,}")

    if data["buckets"]:
        st.subheader("По категориям бюджета")
        rows = []
        for b in data["buckets"]:
            rows.append({
                "Категория": b["bucket"],
                "Отправлено": b["sent"],
                "Найм": b["win"],
                "Проигрыш": b["lost"],
                "Win-rate": b["win_rate"],
                "Выручка": b["revenue"],
                "⚠️": "неэффективна" if b["underperforming"] else "",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # Daily report (Day 10).
    st.subheader("Отчётность")
    rcol1, rcol2 = st.columns([1, 3])
    with rcol1:
        if st.button("📤 Отправить отчёт сейчас"):
            from reporting import send_report

            with st.spinner("Формирую и отправляю отчёт…"):
                res = send_report()
            if res["sent"]:
                st.success("Отчёт отправлен.")
            else:
                st.warning("Отчёт сохранён, но каналы не настроены/недоступны.")
            st.caption(f"telegram: {res['telegram']} · discord: {res['discord']}")
    with rcol2:
        from reporting import list_reports

        _db = get_db_session()
        try:
            reports = list_reports(_db, limit=10)
            if reports:
                with st.expander(f"История отчётов ({len(reports)})"):
                    for rep in reports:
                        st.text(f"{rep.day}")
                        st.code(rep.text or "", language=None)
            else:
                st.caption("Отчётов пока нет.")
        finally:
            _db.close()

    st.divider()

    st.subheader("Последние вакансии")

    if not jobs:
        st.info("Пока нет вакансий. Добавь первую во вкладке «Вакансии».")
        return

    for job in jobs[:10]:
        with st.container(border=True):
            col_a, col_b, col_c = st.columns([3, 1, 1])

            with col_a:
                st.markdown(f"### #{job.id} — {job.title}")
                st.write(job.description[:350] + ("..." if len(job.description) > 350 else ""))

            with col_b:
                st.write("**Бюджет:**")
                st.write(job.budget or "—")
                st.write("**Fit score:**")
                st.write(job.fit_score if job.fit_score is not None else "—")

            with col_c:
                st.write("**Статус:**")
                st.write(job.status)
                st.write("**Решение AI:**")
                st.write(job.ai_decision or "—")

def render_jobs():
    st.title("Вакансии")

    with st.expander("Добавить новую вакансию", expanded=True):
        with st.form("create_job_form"):
            title = st.text_input("Название вакансии")
            budget = st.text_input("Бюджет", placeholder="Например: $5k-$20k или hourly")
            source_url = st.text_input("Ссылка на вакансию", placeholder="https://...")
            description = st.text_area("Описание вакансии", height=250)

            submitted = st.form_submit_button("Сохранить вакансию")

            if submitted:
                if not title.strip():
                    st.error("Нужно указать название вакансии.")
                elif not description.strip():
                    st.error("Нужно вставить описание вакансии.")
                else:
                    res = create_job(
                        title=title.strip(),
                        description=description.strip(),
                        budget=budget.strip(),
                        source_url=source_url.strip(),
                    )
                    if res["added"]:
                        st.success("Вакансия сохранена.")
                        st.rerun()
                    else:
                        st.warning("Дубликат — вакансия с таким URL/ID уже есть.")

    st.divider()

    active = get_active_task()

    # One-shot manual scrape from Upwork. NOT a loop — a single action — so it's
    # allowed in the UI. Blocks ~20-30s (opens a visible browser); must not run
    # while worker.py is active (same Edge profile lock).
    with st.expander("⬇️ Импортировать из Upwork (разовый скрейп)"):
        st.caption(
            "Откроется браузер на ~20–30 сек. Не запускай одновременно с worker — "
            "конфликт профиля Edge. Вакансии привяжутся к активной задаче."
        )
        if st.button("Скрейпить ленту сейчас", disabled=active is None):
            from jobs import ingest_from_upwork

            with st.spinner("Открываю Upwork и читаю ленту…"):
                ok, res = run_browser_op(lambda: ingest_from_upwork(active.id))
            if not ok:
                st.error("Браузер занят (worker или другая операция). Подождите и повторите.")
                res = {}
            if res.get("ok"):
                st.success(
                    f"Добавлено: {res['added']} · дубликатов: {res['skipped_dup']} "
                    f"({res.get('reason', '')})"
                )
            else:
                st.error(f"Не удалось: {res.get('reason')}")
            st.rerun()
        if active is None:
            st.caption("Нет активной задачи — выбери её в разделе «Компании и задачи».")

    with st.expander("🔎 Поиск вакансий по ключевым словам"):
        st.caption("Помимо ленты best-matches — активный поиск по запросу. Открывает браузер.")
        sq = st.text_input("Запрос", placeholder="например: Unity multiplayer Photon")
        if st.button("Искать и импортировать", disabled=active is None or not sq.strip()):
            from jobs import ingest_search

            with st.spinner(f"Ищу «{sq}» на Upwork…"):
                ok, res = run_browser_op(lambda: ingest_search(sq.strip(), active.id))
            if not ok:
                st.error("Браузер занят. Подождите и повторите.")
            elif res.get("ok"):
                st.success(f"Добавлено: {res['added']} · дубликатов: {res['skipped_dup']} ({res.get('reason', '')})")
            else:
                st.error(f"Не удалось: {res.get('reason')}")
            st.rerun()

    with st.expander("🔄 Синхронизировать статусы откликов с Upwork"):
        st.caption("Читает «Submitted proposals» на Upwork и помечает совпадающие вакансии SENT. Открывает браузер.")
        if st.button("Синхронизировать отклики"):
            from proposals_sync import sync_from_upwork

            with st.spinner("Читаю отправленные отклики с Upwork…"):
                ok, res = run_browser_op(sync_from_upwork)
            st.session_state["syncres"] = res if ok else {"ok": False, "reason": "браузер занят"}
            st.rerun()
        _syr = st.session_state.get("syncres")
        if _syr:
            if _syr.get("ok"):
                st.success(f"Найдено: {_syr.get('found', 0)} · совпало: {_syr['matched']} · "
                           f"помечено SENT: {_syr['marked']} · без совпадения: {_syr.get('unmatched', 0)}")
            else:
                st.error(f"Не удалось: {_syr.get('reason')}")

    # Qualify NEW jobs (LLM only — safe to run in Streamlit).
    col_q, col_a, col_f = st.columns([1, 1, 2])
    with col_q:
        if st.button("🧠 Квалифицировать NEW", disabled=active is None):
            from qualify import qualify_new_jobs

            bar, cb = make_progress("Квалификация")
            res = qualify_new_jobs(active.id, progress=cb)
            bar.empty()
            st.session_state["qualres"] = res
            st.rerun()
    with col_a:
        if st.button("🧠 Все задачи"):
            from qualify import qualify_new_jobs

            bar, cb = make_progress("Квалификация (все задачи)")
            res = qualify_new_jobs(None, limit=200, progress=cb)
            bar.empty()
            st.session_state["qualres"] = res
            st.rerun()
    _qr = st.session_state.get("qualres")
    if _qr:
        st.success(
            f"Квалификация — APPLY: {_qr['applied']} · SKIP: {_qr['skipped']} · "
            f"ошибок: {_qr['errors']} (из {_qr['total']})"
        )
    with col_f:
        status_filter = st.selectbox(
            "Фильтр по статусу",
            ["Все", "NEW", "READY_TO_PROPOSE", "PROPOSAL_DRAFTED", "SENT", "SKIPPED", "ERROR"],
        )
    if active is None:
        st.caption("Нет активной задачи — кнопка «Квалифицировать NEW» недоступна; «Все задачи» работает.")

    # Batch auto-submit drafted proposals (opens browser; honors SUBMIT_PER_RUN).
    from submit import auto_submit_enabled, submit_per_run

    live = auto_submit_enabled()
    mode = "🔴 LIVE" if live else "🟡 DRY-RUN"
    st.caption(
        f"Автосабмит: режим {mode}, по {submit_per_run()} за нажатие "
        f"(AUTO_SUBMIT / SUBMIT_PER_RUN в .env). Открывает браузер — не запускай вместе с worker."
    )
    if st.button("🚀 Автосабмит черновиков", disabled=active is None):
        from submit import submit_ready

        bar, cb = make_progress("Автосабмит")
        with st.spinner("Открываю Upwork и отправляю отклики…"):
            ok, res = run_browser_op(lambda: submit_ready(active.id, progress=cb))
        bar.empty()
        st.session_state["batchsubmitres"] = res if ok else {"submitted": 0, "errors": 0, "total": 0, "reason": "браузер занят"}
        st.rerun()

    _bsr = st.session_state.get("batchsubmitres")
    if _bsr:
        sent, dry = _bsr.get("submitted", 0), _bsr.get("dry_run", 0)
        if sent:
            st.success(f"Отправлено: {sent} · ошибок: {_bsr.get('errors', 0)} (из {_bsr.get('total', 0)})")
        elif dry:
            st.info(f"Dry-run: подготовлено {dry} (реально не отправлено; AUTO_SUBMIT=0)")
        else:
            st.warning(f"Ничего не отправлено · ошибок: {_bsr.get('errors', 0)} (из {_bsr.get('total', 0)})")

    jobs = get_jobs()
    if status_filter != "Все":
        # "Готовы откликнуться" — один этап пайплайна: и до, и после генерации
        # черновика. Поэтому READY_TO_PROPOSE включает PROPOSAL_DRAFTED, чтобы
        # вакансия не «исчезала» после генерации отклика.
        if status_filter == "READY_TO_PROPOSE":
            allowed = {"READY_TO_PROPOSE", "PROPOSAL_DRAFTED"}
        else:
            allowed = {status_filter}
        jobs = [j for j in jobs if j.status in allowed]

    st.subheader(f"Список вакансий ({len(jobs)})")

    if not jobs:
        st.info("Вакансий нет под выбранный фильтр.")
        return

    _decision_icon = {"APPLY": "✅ APPLY", "SKIP": "⛔ SKIP"}
    for job in paginate(jobs, "jobs"):
        with st.container(border=True):
            col1, col2, col3 = st.columns([3, 1, 1])

            with col1:
                st.markdown(f"### #{job.id} — {job.title}")
                st.write(job.description[:500] + ("..." if len(job.description) > 500 else ""))

                if job.ai_summary:
                    st.caption(f"AI: {job.ai_summary}")
                if job.skip_reason:
                    st.caption(f"Причина SKIP: {job.skip_reason}")
                if job.source_url:
                    st.write(f"Источник: {job.source_url}")
                with st.expander("✏️ Редактировать / удалить вакансию"):
                    ej_title = st.text_input("Заголовок", value=job.title, key=f"jt_{job.id}")
                    ej_desc = st.text_area("Описание", value=job.description, height=120, key=f"jd_{job.id}")
                    ej_budget = st.text_input("Бюджет", value=job.budget or "", key=f"jb_{job.id}")
                    jc1, jc2 = st.columns(2)
                    if jc1.button("💾 Сохранить", key=f"jsave_{job.id}"):
                        update_row(Job, job.id, title=ej_title.strip(), description=ej_desc.strip(),
                                   budget=ej_budget.strip() or None)
                        st.rerun()
                    if jc2.button("🗑 Удалить вакансию", key=f"jdel_{job.id}"):
                        from database import Proposal as _P
                        _db = get_db_session()
                        try:
                            _db.query(_P).filter(_P.job_id == job.id).delete()
                            _db.commit()
                        finally:
                            _db.close()
                        delete_row(Job, job.id)
                        st.rerun()

            with col2:
                st.write("**Бюджет:**")
                st.write(job.budget or "—")
                st.write("**Решение AI:**")
                st.write(_decision_icon.get(job.ai_decision, "—"))

            with col3:
                st.write("**Статус:**")
                st.write(job.status)
                if job.fit_score is not None:
                    st.write(f"Fit: {job.fit_score}")
                if job.risk_score is not None:
                    st.write(f"Risk: {job.risk_score}")

                # Manual override (operator can flip AI's decision).
                oc1, oc2 = st.columns(2)
                with oc1:
                    if st.button("APPLY", key=f"apply_{job.id}"):
                        override_job_decision(job.id, "APPLY")
                        st.rerun()
                with oc2:
                    if st.button("SKIP", key=f"skip_{job.id}"):
                        override_job_decision(job.id, "SKIP")
                        st.rerun()

            # Proposal block — only for jobs the AI/operator decided to apply to.
            if job.status in ("READY_TO_PROPOSE", "PROPOSAL_DRAFTED"):
                # Fabricate a job-tailored case study (PDF/PNG) to attach as proof.
                if st.button("🧪 Сгенерировать кейс под вакансию", key=f"gencase_{job.id}",
                             help="LLM придумает релевантный кейс и соберёт PDF + инфографику (synthetic)"):
                    with st.spinner("LLM сочиняет кейс и собирает PDF/инфографику…"):
                        cres = generate_case_for_job_ui(job.id)
                    if cres.get("ok"):
                        st.success(f"Кейс готов: «{cres['title']}» (#{cres['case_id']}).")
                    else:
                        st.error(f"Не удалось: {cres.get('reason')}")
                    st.rerun()

                # Generated cases for THIS vacancy — link/preview right here.
                _jcases = get_cases_for_job(job.id)
                if _jcases:
                    import os as _os
                    st.caption(f"Сгенерированные кейсы под эту вакансию ({len(_jcases)}):")
                    for _gc in _jcases:
                        _ap = getattr(_gc, "artifact_path", None)
                        st.markdown(f"- 🧪 **{_gc.title}** (кейс #{_gc.id})")
                        if _ap and _os.path.exists(_ap):
                            with open(_ap, "rb") as _f:
                                st.download_button(
                                    "📄 Скачать PDF", _f.read(),
                                    file_name=_os.path.basename(_ap), mime="application/pdf",
                                    key=f"jdlpdf_{job.id}_{_gc.id}",
                                )
                            _png = _os.path.splitext(_ap)[0] + ".png"
                            if _os.path.exists(_png):
                                st.image(_png, width=420)
                        else:
                            st.caption("  (файл-вложение не найден — перегенерируйте кейс)")

                draft = get_draft_proposal(job.id)
                label = "♻️ Перегенерировать отклик" if draft else "✍️ Сгенерировать отклик"
                if st.button(label, key=f"gen_{job.id}"):
                    with st.spinner("LLM пишет отклик…"):
                        res = generate_proposal_for_job(job.id)
                    if res["status"] == "DRAFT":
                        st.success(f"Черновик готов (кейсы: {res['cases'] or '—'}).")
                    else:
                        st.error("Не удалось сгенерировать (LLM вернул не-JSON).")
                    st.rerun()

                if draft:
                    with st.expander("📝 Отклик (черновик)", expanded=False):
                        import json as _json

                        try:
                            cids = _json.loads(draft.selected_cases or "[]")
                        except ValueError:
                            cids = []
                        st.caption(f"Кейсы в отклике: {cids or '—'}")
                        new_content = st.text_area(
                            "Текст отклика", value=draft.content or "", height=300,
                            key=f"content_{job.id}",
                        )
                        new_estimate = st.text_area(
                            "Смета", value=draft.estimate or "", height=120,
                            key=f"estimate_{job.id}",
                        )
                        psave, pdel = st.columns(2)
                        if psave.button("💾 Сохранить отклик", key=f"savep_{job.id}"):
                            update_proposal_content(draft.id, new_content, new_estimate)
                            st.success("Отклик сохранён.")
                            st.rerun()
                        if pdel.button("🗑 Удалить черновик", key=f"delp_{job.id}"):
                            from database import Proposal as _P
                            delete_row(_P, draft.id)
                            update_row(Job, job.id, status="READY_TO_PROPOSE")
                            st.success("Черновик удалён.")
                            st.rerun()

                        st.divider()
                        from submit import auto_submit_enabled

                        live = auto_submit_enabled()
                        mode = "🔴 LIVE (реальная отправка)" if live else "🟡 DRY-RUN (без отправки)"
                        st.caption(f"Режим: {mode}. Открывает браузер. AUTO_SUBMIT в .env.")
                        if st.button("🚀 Отправить отклик", key=f"submit_{job.id}"):
                            with st.spinner("Открываю форму отклика на Upwork…"):
                                ok, res = run_browser_op(lambda: submit_proposal_for_job(job.id))
                            # Persist result so it survives the rerun (otherwise the
                            # message flashes and disappears → "не понятно, ушло ли").
                            st.session_state[f"submitres_{job.id}"] = res if ok else {
                                "ok": False, "submitted": False, "reason": "браузер занят (worker/др. операция)"}
                            st.rerun()

                        _sr = st.session_state.get(f"submitres_{job.id}")
                        if _sr:
                            if _sr.get("submitted"):
                                st.success(f"✅ Отправлено! Connects: {_sr.get('connects') or '—'} · {_sr.get('reason')}")
                            elif _sr.get("dry_run") and _sr.get("ok"):
                                st.info(f"🟡 Dry-run (не отправлено): {_sr.get('reason')}")
                            else:
                                st.error(f"❌ Не отправлено: {_sr.get('reason')}")

            if job.status == "SENT":
                st.success("✅ Отклик отправлен.")

            # Outcome tracking (Day 9) — available once a proposal exists.
            if job.status in ("SENT", "PROPOSAL_DRAFTED"):
                from learning import set_outcome

                cur = job.outcome or "—"
                st.caption(f"Исход: **{cur}**" + (f" · ${job.revenue:,}" if job.revenue else ""))
                ob = st.columns(4)
                if ob[0].button("💬 Ответил", key=f"o_rep_{job.id}"):
                    _db = get_db_session(); set_outcome(_db, job.id, "REPLIED"); _db.close(); st.rerun()
                if ob[1].button("🎤 Интервью", key=f"o_int_{job.id}"):
                    _db = get_db_session(); set_outcome(_db, job.id, "INTERVIEW"); _db.close(); st.rerun()
                if ob[2].button("⛔ Проигрыш", key=f"o_lost_{job.id}"):
                    _db = get_db_session()
                    with st.spinner("LLM анализирует проигрыш…"):
                        set_outcome(_db, job.id, "LOST")
                    _db.close(); st.rerun()
                with ob[3]:
                    rev = st.number_input("Выручка $", 0, 1_000_000, int(job.revenue or 0),
                                          key=f"rev_{job.id}", label_visibility="collapsed")
                    if st.button("🏆 Найм", key=f"o_win_{job.id}"):
                        _db = get_db_session(); set_outcome(_db, job.id, "WIN", revenue=int(rev)); _db.close(); st.rerun()
                if job.loss_reason:
                    st.warning(f"Разбор проигрыша: {job.loss_reason}")


def render_cases():
    st.title("База кейсов")

    with st.expander("Добавить кейс", expanded=True):
        with st.form("create_case_form"):
            title = st.text_input("Название кейса")
            niche = st.text_input("Ниша", placeholder="Например: Unity multiplayer, mobile app, backend")
            stack = st.text_input("Стек", placeholder="Например: Unity, Photon, Firebase")
            budget_range = st.text_input("Бюджет проекта", placeholder="Например: $5k-$10k")
            url = st.text_input("Ссылка", placeholder="Ссылка на портфолио / demo / GitHub")
            description = st.text_area("Описание кейса", height=180)
            result = st.text_area("Результат", height=120)

            submitted = st.form_submit_button("Сохранить кейс")

            if submitted:
                if not title.strip():
                    st.error("Нужно указать название кейса.")
                elif not description.strip():
                    st.error("Нужно добавить описание кейса.")
                else:
                    create_case_study(
                        title=title.strip(),
                        niche=niche.strip(),
                        stack=stack.strip(),
                        description=description.strip(),
                        result=result.strip(),
                        budget_range=budget_range.strip(),
                        url=url.strip(),
                    )
                    st.success("Кейс сохранён.")
                    st.rerun()

    st.divider()

    cases = get_case_studies()

    st.subheader("Список кейсов")

    if not cases:
        st.info("Кейсов пока нет.")
        return

    for case in paginate(cases, "cases"):
        with st.container(border=True):
            badge = " 🧪 _synthetic_" if getattr(case, "synthetic", 0) else ""
            st.markdown(f"### #{case.id} — {case.title}{badge}")
            if getattr(case, "synthetic", 0) and getattr(case, "job_id", None):
                st.caption(f"Сгенерирован под вакансию #{case.job_id} (вымышленный кейс — ответственность на владельце аккаунта)")

            # Download the generated attachment (PDF + infographic), if present.
            import os as _os

            apath = getattr(case, "artifact_path", None)
            if apath and _os.path.exists(apath):
                dc1, dc2 = st.columns(2)
                with dc1, open(apath, "rb") as _f:
                    st.download_button("📄 Скачать PDF-кейс", _f.read(),
                                       file_name=_os.path.basename(apath), mime="application/pdf",
                                       key=f"dlpdf_{case.id}")
                png = _os.path.splitext(apath)[0] + ".png"
                if _os.path.exists(png):
                    with dc2:
                        st.image(png, caption="Инфографика кейса", use_container_width=True)

            col1, col2, col3 = st.columns(3)

            with col1:
                st.write("**Ниша:**")
                st.write(case.niche or "—")

            with col2:
                st.write("**Стек:**")
                st.write(case.stack or "—")

            with col3:
                st.write("**Бюджет:**")
                st.write(case.budget_range or "—")

            st.write("**Описание:**")
            st.write(case.description)

            if case.result:
                st.write("**Результат:**")
                st.write(case.result)

            if case.url:
                st.write(f"**Ссылка:** {case.url}")

            with st.expander("✏️ Редактировать / удалить"):
                with st.form(f"edit_case_{case.id}"):
                    e_title = st.text_input("Название", value=case.title)
                    e_niche = st.text_input("Ниша", value=case.niche or "")
                    e_stack = st.text_input("Стек", value=case.stack or "")
                    e_budget = st.text_input("Бюджет", value=case.budget_range or "")
                    e_url = st.text_input("Ссылка", value=case.url or "")
                    e_desc = st.text_area("Описание", value=case.description, height=150)
                    e_result = st.text_area("Результат", value=case.result or "", height=100)
                    if st.form_submit_button("Сохранить изменения"):
                        if not e_title.strip() or not e_desc.strip():
                            st.error("Название и описание обязательны.")
                        else:
                            update_case_study(
                                case.id,
                                title=e_title.strip(),
                                niche=e_niche.strip(),
                                stack=e_stack.strip(),
                                budget_range=e_budget.strip(),
                                url=e_url.strip(),
                                description=e_desc.strip(),
                                result=e_result.strip(),
                            )
                            st.success("Изменения сохранены.")
                            st.rerun()
                if st.button("🗑 Удалить кейс", key=f"del_case_{case.id}"):
                    mode = delete_case_study(case.id)
                    if mode == "soft":
                        st.warning("Кейс используется в отклике — скрыт (soft-delete).")
                    else:
                        st.success("Кейс удалён.")
                    st.rerun()


def render_clients():
    import clients as C

    st.title("Клиенты")
    st.caption(
        "Карточка на каждый ответ заказчика. При включённом AI — черновик ответа; "
        "оператор редактирует и отправляет, либо отключает AI и пишет сам."
    )

    with st.expander("⬇️ Импортировать сообщения из Upwork (разовый)"):
        st.caption(
            "Откроет браузер на ~30–60 сек, прочитает инбокс через сессию Upwork, "
            "создаст карточки и AI-черновики. Не запускай вместе с worker."
        )
        n_rooms = st.number_input("Сколько диалогов тянуть", 1, 50, 10)
        if st.button("Импортировать инбокс"):
            from messages import import_messages

            bar, cb = make_progress("Импорт диалогов")
            with st.spinner("Читаю переписку из Upwork…"):
                ok, res = run_browser_op(lambda: import_messages(max_rooms=int(n_rooms), progress=cb))
            bar.empty()
            if not ok:
                res = {"ok": False, "reason": "браузер занят (worker/др. операция)"}
            if res.get("ok"):
                st.success(
                    f"Диалогов: {res['rooms']} · новых сообщений: {res['imported']} · "
                    f"дублей пропущено: {res['skipped']}"
                )
            else:
                st.error(f"Не удалось: {res.get('reason')}")
            st.rerun()

    with st.expander("➕ Импортировать сообщение клиента (вручную)", expanded=False):
        with st.form("inbound_form"):
            in_name = st.text_input("Имя/идентификатор клиента")
            in_content = st.text_area("Текст сообщения клиента", height=120)
            if st.form_submit_button("Добавить сообщение"):
                if not in_content.strip():
                    st.error("Нужен текст сообщения.")
                else:
                    db = get_db_session()
                    try:
                        with st.spinner("Создаю карточку и черновик ответа…"):
                            C.record_inbound(db, in_content.strip(), name=in_name.strip() or None)
                    finally:
                        db.close()
                    st.success("Сообщение добавлено, черновик готов.")
                    st.rerun()

    st.divider()
    db = get_db_session()
    try:
        clients = C.list_clients(db)
        if not clients:
            st.info("Пока нет клиентов. Импортируй первое сообщение выше.")
            return
        for client in paginate(clients, "clients"):
            label = client.name or f"Клиент #{client.id}"
            with st.container(border=True):
                top1, top2 = st.columns([3, 1])
                with top1:
                    st.markdown(f"### {label}  ·  _{client.status}_")
                with top2:
                    ai_on = st.toggle("AI", value=bool(client.ai_enabled), key=f"ai_{client.id}")
                    if ai_on != bool(client.ai_enabled):
                        C.set_ai_enabled(db, client.id, ai_on)
                        st.rerun()

                history = C.get_messages(db, client.id)
                from database import ClientMessage as _CM

                for m in history:
                    who = "🧑‍💼 Клиент" if m.direction == "inbound" else "📤 Мы"
                    st.markdown(f"**{who}:** {m.content}")
                    edit_delete_controls(
                        f"cm_{m.id}", m.content,
                        on_save=lambda v, mid=m.id: update_row(_CM, mid, content=v),
                        on_delete=lambda mid=m.id: delete_row(_CM, mid),
                        label="Сообщение",
                    )

                last = history[-1] if history else None
                reply_key = f"reply_{client.id}"
                # A regenerated draft (set on the previous run) overrides the field;
                # otherwise seed from the latest inbound's stored AI draft once.
                pending = st.session_state.pop(f"pending_{client.id}", None)
                if pending is not None:
                    st.session_state[reply_key] = pending
                elif reply_key not in st.session_state:
                    st.session_state[reply_key] = (
                        last.ai_draft if (last is not None and last.direction == "inbound" and last.ai_draft) else ""
                    )

                reply = st.text_area("Ответ", height=140, key=reply_key)
                from messages import msg_auto_send_enabled

                send_live = msg_auto_send_enabled()
                send_label = "📨 Отправить в Upwork" if (client.external_id and send_live) else "📨 Отправить"
                rc1, rc2 = st.columns(2)
                with rc1:
                    if st.button(send_label, key=f"send_{client.id}"):
                        _last_out = C.last_outbound(db, client.id)
                        if not reply.strip():
                            st.error("Пустой ответ.")
                        elif _last_out is not None and (_last_out.content or "").strip() == reply.strip():
                            st.warning("Это же сообщение уже отправлено последним — повтор отменён, чтобы не дублировать у клиента.")
                        elif client.external_id:
                            from messages import send_message

                            with st.spinner("Отправляю через Upwork…"):
                                ok_lock, res = run_browser_op(lambda: send_message(client.external_id, reply.strip()))
                            if not ok_lock:
                                res = {"ok": False, "sent": False, "reason": "браузер занят (worker/др. операция)"}
                            if res.get("sent"):
                                C.send_reply(db, client.id, reply.strip())
                                st.session_state.pop(reply_key, None)
                                st.success("Отправлено в Upwork ✅")
                                st.rerun()
                            elif res.get("dry_run") and res.get("ok"):
                                st.info("DRY-RUN: в Upwork не отправлено (MSG_AUTO_SEND=0). Текст подготовлен в поле ввода.")
                            else:
                                st.error(f"Не отправлено: {res.get('reason')}")
                        else:
                            # Manual client without a linked Upwork room.
                            C.send_reply(db, client.id, reply.strip())
                            st.session_state.pop(reply_key, None)
                            st.success("Записано локально (нет привязки к Upwork-комнате).")
                            st.rerun()
                    if client.external_id:
                        st.caption(f"Режим отправки: {'🔴 LIVE' if send_live else '🟡 DRY-RUN'} (MSG_AUTO_SEND в .env)")
                with rc2:
                    if client.ai_enabled and st.button("♻️ Перегенерировать черновик", key=f"regen_{client.id}"):
                        with st.spinner("LLM пишет черновик…"):
                            try:
                                text = C.generate_draft_text(client, db)
                            except Exception as exc:  # noqa: BLE001
                                text = f"[Ошибка LLM: {exc}]"
                        st.session_state[f"pending_{client.id}"] = text  # shown next run
                        st.rerun()
    finally:
        db.close()


def get_agent_chat(task_id):
    from database import AgentChatMessage

    db = get_db_session()
    try:
        q = db.query(AgentChatMessage)
        q = q.filter(AgentChatMessage.task_id == task_id) if task_id is not None \
            else q.filter(AgentChatMessage.task_id.is_(None))
        return q.order_by(AgentChatMessage.created_at.asc(), AgentChatMessage.id.asc()).all()
    finally:
        db.close()


def add_agent_chat(task_id, role, content):
    from database import AgentChatMessage

    db = get_db_session()
    try:
        db.add(AgentChatMessage(task_id=task_id, role=role, content=content))
        db.commit()
    finally:
        db.close()


def clear_agent_chat(task_id):
    from database import AgentChatMessage

    db = get_db_session()
    try:
        q = db.query(AgentChatMessage)
        q = q.filter(AgentChatMessage.task_id == task_id) if task_id is not None \
            else q.filter(AgentChatMessage.task_id.is_(None))
        q.delete()
        db.commit()
    finally:
        db.close()


def build_agent_chat_context():
    """Maximal grounding for the agent chat: strategy + funnel + outcomes + losses + cases + open questions + balance."""
    import learning as L
    from analytics import compute_metrics, analytics_by_bucket
    from database import Job, CaseStudy

    lines = []
    db = get_db_session()
    try:
        active = get_active_task()
        tid = active.id if active else None
        if active:
            strat = (active.strategy or active.description or "").strip()
            lines.append(f"АКТИВНАЯ ЗАДАЧА: #{active.id} {active.name}")
            if strat:
                lines.append(f"СТРАТЕГИЯ:\n{strat}")
        else:
            lines.append("Активной задачи нет (выбери в «Компании и задачи»).")

        m = compute_metrics(db, tid)
        lines.append(
            f"ВОРОНКА: отклики={m['proposals_sent']}, connects потрачено={m['connects_spent']}, "
            f"ответы={m['replies']}, интервью={m['interviews']}, найм={m['hires']}, "
            f"выручка=${m['revenue']}, проигрыши={m['lost']}"
        )
        under = [b["bucket"] for b in analytics_by_bucket(db, tid) if b["underperforming"]]
        if under:
            lines.append("НЕЭФФЕКТИВНЫЕ КАТЕГОРИИ (≥3 отправок, 0 наймов): " + ", ".join(under))

        jq = db.query(Job)
        if tid is not None:
            jq = jq.filter(Job.task_id == tid)
        recent = jq.order_by(Job.created_at.desc()).limit(15).all()
        if recent:
            lines.append("ПОСЛЕДНИЕ ВАКАНСИИ:")
            for j in recent:
                s = f"- #{j.id} [{j.status}] {(j.title or '')[:65]}"
                if j.outcome:
                    s += f" · исход={j.outcome}" + (f" ${j.revenue}" if j.revenue else "")
                lines.append(s)
        losses = [j for j in recent if j.loss_reason]
        if losses:
            lines.append("РАЗБОРЫ ПРОИГРЫШЕЙ:")
            for j in losses[:5]:
                lines.append(f"- #{j.id}: {(j.loss_reason or '')[:220]}")

        cases = db.query(CaseStudy).filter(CaseStudy.deleted == 0).all()
        if cases:
            niches = sorted({(c.niche or "").strip() for c in cases if c.niche})
            syn = sum(1 for c in cases if getattr(c, "synthetic", 0))
            lines.append(f"КЕЙСОВ В БАЗЕ: {len(cases)} (из них synthetic: {syn})"
                         + (f"; ниши: {', '.join(list(niches)[:8])}" if niches else ""))

        open_qs = L.list_questions(db, status="open")
        if open_qs:
            lines.append("ОТКРЫТЫЕ ВОПРОСЫ АГЕНТА (что стоит уточнить у оператора):")
            for q in open_qs[:8]:
                lines.append(f"- {(q.text or '')[:160]}")

        # Recent client conversations (so the agent can advise on negotiations).
        try:
            import clients as C

            cl = C.list_clients(db)
            if cl:
                lines.append("ПЕРЕПИСКА С КЛИЕНТАМИ (последние сообщения):")
                for c in cl[:6]:
                    name = c.name or f"Клиент #{c.id}"
                    msgs = C.get_messages(db, c.id)
                    waiting = bool(msgs) and msgs[-1].direction == "inbound"
                    head = f"- {name} [{c.status}]" + (" · ЖДЁТ ОТВЕТА" if waiting else "")
                    lines.append(head)
                    for msg in msgs[-3:]:
                        who = "Клиент" if msg.direction == "inbound" else "Мы"
                        lines.append(f"    {who}: {(msg.content or '')[:160]}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()

    try:
        from connects import read_balance

        bal = read_balance()
        if bal:
            lines.append(f"БАЛАНС CONNECTS: {bal['balance']} (на {bal['updated_at'][:16]})")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def render_agent_chat():
    import learning as L

    st.title("Чат с агентом")
    st.caption("Спросите агента что угодно — он отвечает с учётом стратегии, метрик, исходов и кейсов. История сохраняется.")

    active = get_active_task()
    tid = active.id if active else None

    db = get_db_session()
    try:
        open_qs = L.list_questions(db, status="open")
    finally:
        db.close()
    if open_qs:
        with st.expander(f"❓ Агент хочет уточнить ({len(open_qs)})"):
            for q in open_qs:
                st.markdown(f"- {q.text}")
            st.caption("Ответь на них прямо в чате ниже.")

    import agent_tools

    with st.expander("🔧 Что умеет агент (команды)"):
        st.markdown(
            "Можно попросить выполнить действие, например:\n"
            "- «квалифицируй новые вакансии»\n"
            "- «сгенерируй черновики откликов»\n"
            "- «сделай черновик ответа клиенту Ammaniel»\n"
            "- «сгенерируй кейс под вакансию 83»\n"
            "- «отправь дневной отчёт»\n"
            "- «импортируй вакансии из ленты» · «найди вакансии Photon multiplayer» · «импортируй инбокс» "
            "(браузерные — откроют Edge на ~30–60с, идут под общим локом, не конфликтуют с worker)\n\n"
            "Отправка откликов и сообщений клиентам в чат-инструменты НЕ входит "
            "(необратимо) — это делается в «Вакансии»/«Клиенты»."
        )

    history = get_agent_chat(tid)
    for msg in history:
        with st.chat_message(msg.role):
            st.write(msg.content)

    if history and st.button("🗑 Очистить историю чата"):
        clear_agent_chat(tid)
        st.rerun()

    prompt = st.chat_input("Спросите агента или дайте команду…")
    if prompt:
        from ai import call_llm

        add_agent_chat(tid, "user", prompt)
        hist = [(m.role, m.content) for m in get_agent_chat(tid)]
        with st.spinner("Агент думает…"):
            try:
                decision = agent_tools.select_action(prompt, hist, build_agent_chat_context(), call_llm)
            except Exception as exc:  # noqa: BLE001
                decision = {"action": "none", "args": {}, "reply": f"[Ошибка LLM: {exc}]"}
        reply = decision.get("reply") or ""
        action = decision.get("action", "none")
        if action and action != "none":
            with st.spinner(f"Выполняю: {action}…"):
                res = agent_tools.run_action(action, decision.get("args", {}), tid)
            mark = "✅" if res.get("ok") else "⚠️"
            reply = (reply + "\n\n" if reply else "") + f"🔧 {mark} {res['summary']}"
        add_agent_chat(tid, "assistant", reply or "(пустой ответ)")
        st.rerun()


page = st.sidebar.radio(
    "Разделы",
    [
        "Dashboard",
        "Компании и задачи",
        "Вакансии",
        "Кейсы",
        "Клиенты",
        "Чат с агентом",
    ],
)

_active = get_active_task()
st.sidebar.caption(
    f"Активная задача: #{_active.id} — {_active.name}" if _active else "Активной задачи нет"
)

# Worker status (read-only; worker runs as a separate process — `python worker.py`)
from worker import read_status as _read_worker_status  # noqa: E402

_ws = _read_worker_status()
st.sidebar.divider()
if not _ws:
    st.sidebar.caption("Worker: не запущен (`python worker.py`)")
else:
    _state = _ws.get("state", "?")
    _sess = _ws.get("session_ok")
    _sess_label = "✅ session" if _sess else ("❌ session" if _sess is False else "session ?")
    st.sidebar.caption(
        f"Worker: {_state} · {_sess_label}\nheartbeat: {_ws.get('heartbeat', '—')}"
    )

if page == "Dashboard":
    render_dashboard()
elif page == "Компании и задачи":
    render_companies_tasks()
elif page == "Вакансии":
    render_jobs()
elif page == "Кейсы":
    render_cases()
elif page == "Клиенты":
    render_clients()
elif page == "Чат с агентом":
    render_agent_chat()