"""Day 8 verification — client conversations.

Covers: client created on first inbound; AI draft generated when ai_enabled;
no draft when AI off; conversation maps inbound→user/outbound→assistant;
append-only history; manual outbound reply.
"""

import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
from database import Base, Client, ClientMessage  # noqa: E402
import clients  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    TestSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(clients, "get_db_session", TestSession)
    session = TestSession()
    yield session
    session.close()


def test_inbound_creates_client_and_draft(db):
    llm = lambda messages: "Thanks! When are you free for a quick call?"
    res = clients.record_inbound(db, "Hi, are you available?", name="Acme", llm=llm)

    assert res["draft"].startswith("Thanks!")
    client = db.query(Client).filter(Client.id == res["client_id"]).first()
    assert client.name == "Acme"
    assert client.status == "REPLIED"
    msgs = clients.get_messages(db, client.id)
    assert len(msgs) == 1
    assert msgs[0].direction == "inbound"
    assert msgs[0].ai_draft.startswith("Thanks!")


def test_no_draft_when_ai_disabled(db):
    client = clients.get_or_create_client(db, "Bob")
    clients.set_ai_enabled(db, client.id, False)
    called = {"n": 0}

    def llm(m):
        called["n"] += 1
        return "draft"

    res = clients.record_inbound(db, "hello", name="Bob", llm=llm)
    assert res["draft"] is None
    assert called["n"] == 0  # LLM not invoked


def test_conversation_maps_roles(db):
    client = clients.get_or_create_client(db, "Acme")
    clients.add_message(db, client.id, "inbound", "first from client")
    clients.add_message(db, client.id, "outbound", "our reply")
    clients.add_message(db, client.id, "inbound", "second from client")

    captured = {}

    def llm(messages):
        captured["messages"] = messages
        return "ok"

    clients.generate_draft_text(client, db, llm=llm)
    roles = [m["role"] for m in captured["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_history_is_append_only(db):
    client = clients.get_or_create_client(db, "Acme")
    clients.record_inbound(db, "msg1", name="Acme", llm=lambda m: "d1")
    clients.send_reply(db, client.id, "reply1")
    clients.record_inbound(db, "msg2", name="Acme", llm=lambda m: "d2")

    msgs = clients.get_messages(db, client.id)
    assert [m.direction for m in msgs] == ["inbound", "outbound", "inbound"]
    assert [m.content for m in msgs] == ["msg1", "reply1", "msg2"]


def test_get_or_create_is_idempotent(db):
    c1 = clients.get_or_create_client(db, "Acme", job_id=5)
    c2 = clients.get_or_create_client(db, "Acme", job_id=5)
    assert c1.id == c2.id
    assert db.query(Client).count() == 1
