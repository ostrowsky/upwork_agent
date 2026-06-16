"""Day 8.5 — Upwork message import (pure ingest logic, no browser).

Verified against the real /api/v3/rooms API: inbound iff userId==targetUserId,
system stories skipped, dedup by storyId, draft on latest unanswered inbound.
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
import messages  # noqa: E402


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


CLIENT_UID = "1008054369112457216"   # == targetUserId
OUR_UID = "1621519344350900224"


def _thread(stories):
    return {
        "room_id": "room_abc",
        "name": "Ammaniel Araia, Software Co",
        "target_user_id": CLIENT_UID,
        "stories": stories,
    }


def test_classify_and_filters():
    assert messages.classify_direction({"userId": CLIENT_UID}, CLIENT_UID) == "inbound"
    assert messages.classify_direction({"userId": OUR_UID}, CLIENT_UID) == "outbound"
    assert messages.is_importable({"message": "hi", "isSystemStory": 0}) is True
    assert messages.is_importable({"message": "", "isSystemStory": 0}) is False
    assert messages.is_importable({"message": "x", "isSystemStory": 1}) is False


def test_ingest_thread_maps_directions_and_drafts(db):
    stories = [
        {"storyId": "s1", "userId": OUR_UID, "message": "Hello! Yes we are available.", "created": 1},
        {"storyId": "s2", "userId": CLIENT_UID, "message": "Do you know when?", "created": 2},
        {"storyId": "sys", "userId": OUR_UID, "message": "joined", "isSystemStory": 1, "created": 3},
    ]
    res = messages.ingest_threads([_thread(stories)], db, llm=lambda m: "In a few days!")
    assert res["imported"] == 2  # system story skipped
    assert res["clients"] == 1

    client = db.query(Client).filter(Client.external_id == "room_abc").first()
    msgs = clients.get_messages(db, client.id)
    assert [m.direction for m in msgs] == ["outbound", "inbound"]
    # latest is inbound → draft generated
    assert msgs[-1].ai_draft == "In a few days!"


def test_ingest_is_idempotent(db):
    stories = [{"storyId": "s1", "userId": CLIENT_UID, "message": "Hi", "created": 1}]
    messages.ingest_threads([_thread(stories)], db, llm=lambda m: "d")
    res2 = messages.ingest_threads([_thread(stories)], db, llm=lambda m: "d")
    assert res2["imported"] == 0
    assert res2["skipped"] == 1
    client = db.query(Client).filter(Client.external_id == "room_abc").first()
    assert db.query(ClientMessage).filter(ClientMessage.client_id == client.id).count() == 1


def test_local_send_reconciled_not_duplicated(db):
    """A reply sent from the UI (no storyId) must not duplicate when it returns from the API."""
    client = clients.get_or_create_client(db, name="Acme", external_id="room_abc")
    # UI send recorded locally without a storyId; whitespace differs from the echo.
    clients.send_reply(db, client.id, "Thanks! Could you share the scope?  ")

    stories = [
        {"storyId": "echo1", "userId": OUR_UID, "message": "Thanks! Could you share the scope?", "created": 5},
    ]
    res = messages.ingest_threads([_thread(stories)], db, llm=lambda m: "x")

    msgs = clients.get_messages(db, client.id)
    assert len(msgs) == 1               # reconciled, not duplicated
    assert msgs[0].external_id == "echo1"  # storyId backfilled onto the local row
    assert res["imported"] == 0 and res["skipped"] == 1


def test_last_outbound(db):
    client = clients.get_or_create_client(db, name="Acme", external_id="room_x")
    assert clients.last_outbound(db, client.id) is None
    clients.add_message(db, client.id, "inbound", "hi")
    clients.send_reply(db, client.id, "first")
    clients.send_reply(db, client.id, "second")
    assert clients.last_outbound(db, client.id).content == "second"


def test_no_draft_when_latest_is_outbound(db):
    stories = [
        {"storyId": "s1", "userId": CLIENT_UID, "message": "Hi", "created": 1},
        {"storyId": "s2", "userId": OUR_UID, "message": "Hello back", "created": 2},
    ]
    called = {"n": 0}

    def llm(m):
        called["n"] += 1
        return "x"

    messages.ingest_threads([_thread(stories)], db, llm=llm)
    assert called["n"] == 0  # latest is our outbound → no draft needed
