"""Client conversations — a client card per contact, AI-drafted replies.

An inbound client message creates/updates a Client card and (when ai_enabled)
generates a reply draft. The operator can edit/send it, or turn AI off and write
manually. Messages are append-only.

MVP: inbound messages are imported manually in the UI (full Upwork inbox sync
without a browser is a non-goal). The LLM/draft logic here is unit-testable.
"""
from __future__ import annotations

from database import get_db_session, Client, ClientMessage  # noqa: F401 — get_db_session is a test monkeypatch seam


CHAT_HISTORY_WINDOW = 16

REPLY_SYSTEM = (
    "Ты — представитель геймдев-студии на Upwork, ведёшь переписку с клиентом "
    "после отклика. Пиши кратко, профессионально и по делу, на языке клиента "
    "(EN или RU). Двигай разговор к следующему шагу (созвон, уточнение скоупа, "
    "оффер). Не обещай того, чего не знаешь; задавай уточняющие вопросы при "
    "нехватке деталей."
)


def get_or_create_client(db, name: str | None, job_id: int | None = None,
                         external_id: str | None = None) -> Client:
    """Find a client by external_id (preferred) or (name, job_id), else create."""
    client = None
    if external_id:
        client = db.query(Client).filter(Client.external_id == external_id).first()
    if client is None and (name or job_id is not None):
        q = db.query(Client)
        if name:
            q = q.filter(Client.name == name)
        if job_id is not None:
            q = q.filter(Client.job_id == job_id)
        client = q.first()
    if client is None:
        client = Client(name=name or None, job_id=job_id, external_id=external_id,
                        status="NEW", ai_enabled=1)
        db.add(client)
        db.commit()
        db.refresh(client)
    return client


def message_exists(db, external_id: str) -> bool:
    if not external_id:
        return False
    return db.query(ClientMessage).filter(ClientMessage.external_id == external_id).first() is not None


def _norm_text(s: str | None) -> str:
    """Collapse whitespace so a local send matches its API echo (Upwork trims/normalizes)."""
    return " ".join((s or "").split())


def find_unsynced_outbound(db, client_id: int, content: str):
    """A locally-recorded outbound (no storyId yet) matching this content.

    Matches on whitespace-normalized text (Upwork may trim trailing spaces /
    rewrap newlines), so reconciling our own sent message backfills its storyId
    instead of the import creating a duplicate row.
    """
    target = _norm_text(content)
    if not target:
        return None
    rows = (
        db.query(ClientMessage)
        .filter(
            ClientMessage.client_id == client_id,
            ClientMessage.direction == "outbound",
            ClientMessage.external_id.is_(None),
        )
        .order_by(ClientMessage.id.asc())
        .all()
    )
    for m in rows:
        if _norm_text(m.content) == target:
            return m
    return None


def last_outbound(db, client_id: int):
    """The most recent outbound message for a client, or None."""
    return (
        db.query(ClientMessage)
        .filter(ClientMessage.client_id == client_id, ClientMessage.direction == "outbound")
        .order_by(ClientMessage.id.desc())
        .first()
    )


def get_messages(db, client_id: int):
    return (
        db.query(ClientMessage)
        .filter(ClientMessage.client_id == client_id)
        .order_by(ClientMessage.created_at.asc(), ClientMessage.id.asc())
        .all()
    )


def add_message(db, client_id: int, direction: str, content: str,
                ai_draft: str | None = None, external_id: str | None = None):
    msg = ClientMessage(
        client_id=client_id, direction=direction, content=content,
        ai_draft=ai_draft, external_id=external_id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def conversation_language(history) -> str:
    """Language of the conversation — prefer the latest inbound, else any text."""
    from proposals import detect_language

    for m in reversed(history):
        if m.direction == "inbound" and (m.content or "").strip():
            return detect_language(m.content)
    sample = " ".join((m.content or "") for m in history)
    return detect_language(sample)


def build_reply_messages(client: Client, history) -> list[dict]:
    """LLM messages from conversation history (inbound=user, outbound=assistant)."""
    lang = conversation_language(history)
    system = REPLY_SYSTEM + (
        f"\n\nОТВЕЧАЙ СТРОГО на языке переписки: {lang}. "
        "Не переключай язык, даже если системный промпт на русском."
    )
    if client is not None and client.notes:
        system += f"\n\nКонтекст клиента: {client.notes}"
    messages = [{"role": "system", "content": system}]
    for m in history[-CHAT_HISTORY_WINDOW:]:
        role = "user" if m.direction == "inbound" else "assistant"
        messages.append({"role": role, "content": m.content})
    return messages


def generate_draft_text(client: Client, db, llm=None) -> str:
    """Generate a reply draft from the conversation. Returns the draft text."""
    if llm is None:
        from ai import call_llm as llm
    history = get_messages(db, client.id)
    return llm(build_reply_messages(client, history))


def record_inbound(db, content: str, name: str | None = None, job_id: int | None = None,
                   llm=None) -> dict:
    """Register an inbound client message; draft a reply if AI is enabled.

    Returns {"client_id", "message_id", "draft"}. The draft (if any) is stored
    on the inbound message's ai_draft column.
    """
    client = get_or_create_client(db, name, job_id)
    if client.status == "NEW":
        client.status = "REPLIED"
    msg = add_message(db, client.id, "inbound", content)

    draft = None
    if client.ai_enabled:
        try:
            draft = generate_draft_text(client, db, llm=llm)
        except Exception as e:  # noqa: BLE001 — never lose the inbound message
            draft = f"[Ошибка LLM: {e}]"
        msg.ai_draft = draft
        db.commit()
    return {"client_id": client.id, "message_id": msg.id, "draft": draft}


def send_reply(db, client_id: int, content: str):
    """Operator-approved outbound reply (append-only)."""
    return add_message(db, client_id, "outbound", content)


def set_ai_enabled(db, client_id: int, enabled: bool):
    client = db.query(Client).filter(Client.id == client_id).first()
    if client:
        client.ai_enabled = 1 if enabled else 0
        db.commit()
    return client


def list_clients(db):
    return db.query(Client).order_by(Client.updated_at.desc(), Client.id.desc()).all()
