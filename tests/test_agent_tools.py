"""Agent chat tools — action parsing, selection, and safe dispatch."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools  # noqa: E402


def test_parse_action_plain_and_fenced():
    a = agent_tools.parse_action('{"action":"qualify_new","args":{},"reply":"Запускаю"}')
    assert a["action"] == "qualify_new" and a["reply"] == "Запускаю"
    b = agent_tools.parse_action('```json\n{"action":"none","args":{},"reply":"hi"}\n```')
    assert b["action"] == "none" and b["reply"] == "hi"


def test_parse_action_unknown_becomes_none():
    a = agent_tools.parse_action('{"action":"delete_everything","args":{},"reply":"no"}')
    assert a["action"] == "none"


def test_parse_action_non_json_is_plain_reply():
    a = agent_tools.parse_action("просто текст без json")
    assert a["action"] == "none"
    assert a["reply"] == "просто текст без json"


def test_select_action_uses_llm(monkeypatch):
    captured = {}

    def fake_llm(messages):
        captured["sys"] = messages[0]["content"]
        captured["user"] = messages[-1]["content"]
        return '{"action":"generate_drafts","args":{},"reply":"Готовлю черновики"}'

    out = agent_tools.select_action("сделай черновики", [], "CTX", fake_llm)
    assert out["action"] == "generate_drafts"
    assert "ИНСТРУМЕНТЫ" in captured["sys"] and "CTX" in captured["sys"]
    assert captured["user"] == "сделай черновики"


def test_update_strategy_writes_canonical():
    import types

    saved = {}

    class FakeTask:
        def __init__(self): self.strategy = "old"

    task = FakeTask()

    class FakeDB:
        def query(self, model): return self
        def filter(self, *a, **k): return self
        def first(self): return task
        def commit(self): saved["committed"] = True

    res = agent_tools.run_action("update_strategy",
                                 {"text": "Unity, $5k–$25k, без NFT, фокус Photon"},
                                 task_id=1, db=FakeDB())
    assert res["ok"] is True
    assert task.strategy == "Unity, $5k–$25k, без NFT, фокус Photon"
    assert saved.get("committed")


def test_update_strategy_requires_text():
    res = agent_tools.run_action("update_strategy", {"text": ""}, task_id=1, db=object())
    assert res["ok"] is False and "Не указан" in res["summary"]


def test_run_action_unknown():
    res = agent_tools.run_action("nope", {}, task_id=1, db=object())
    assert res["ok"] is False and "Неизвестный" in res["summary"]


def test_run_action_qualify(monkeypatch):
    import qualify

    monkeypatch.setattr(qualify, "qualify_new_jobs",
                        lambda task_id, db=None: {"applied": 2, "skipped": 1, "errors": 0, "total": 3})
    res = agent_tools.run_action("qualify_new", {}, task_id=1, db=object())
    assert res["ok"] is True and "APPLY 2" in res["summary"]


def test_run_action_never_raises(monkeypatch):
    import qualify

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(qualify, "qualify_new_jobs", boom)
    res = agent_tools.run_action("qualify_new", {}, task_id=1, db=object())
    assert res["ok"] is False and "Ошибка инструмента" in res["summary"]


def test_import_jobs_busy_browser(monkeypatch):
    import browser_lock

    monkeypatch.setattr(browser_lock, "is_busy", lambda: True)  # worker holds the lock
    res = agent_tools.run_action("import_jobs", {}, task_id=1, db=object())
    assert res["ok"] is False and "занят" in res["summary"]


def test_import_jobs_runs_under_lock(monkeypatch):
    import browser_lock
    import jobs

    events = []
    monkeypatch.setattr(browser_lock, "is_busy", lambda: False)
    monkeypatch.setattr(browser_lock, "acquire", lambda owner, **k: events.append(("acq", owner)) or True)
    monkeypatch.setattr(browser_lock, "release", lambda: events.append(("rel",)))
    monkeypatch.setattr(jobs, "ingest_from_upwork",
                        lambda task_id: {"ok": True, "added": 4, "skipped_dup": 16, "reason": "20 jobs"})

    res = agent_tools.run_action("import_jobs", {}, task_id=1, db=object())
    assert res["ok"] is True and "добавлено 4" in res["summary"]
    assert events[0][0] == "acq" and events[-1][0] == "rel"  # lock acquired then released


def test_import_jobs_no_active_task():
    res = agent_tools.run_action("import_jobs", {}, task_id=None, db=object())
    assert res["ok"] is False and "активной задачи" in res["summary"]


def test_search_jobs_requires_query(monkeypatch):
    import browser_lock

    monkeypatch.setattr(browser_lock, "is_busy", lambda: False)
    res = agent_tools.run_action("search_jobs", {"query": ""}, task_id=1, db=object())
    assert res["ok"] is False and "запрос" in res["summary"]


def test_update_strategy_writes_the_task_it_was_given(tmp_path, monkeypatch):
    """One source of truth: the strategy lives on the Task row, and the write
    must land on the task that was asked about — editing task #2's strategy
    must not rewrite task #1's."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import database
    from database import Base, Company, Task

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", Session)

    db = Session()
    db.add(Company(id=1, name="S"))
    db.commit()
    db.add_all([Task(id=1, company_id=1, name="Unity", strategy="ORIGINAL ONE", is_active=1),
                Task(id=2, company_id=1, name="Art", strategy="ORIGINAL TWO", is_active=0)])
    db.commit()

    res = agent_tools.run_action("update_strategy", {"text": "NEW FOR TWO"}, task_id=2, db=db)

    assert res["ok"], res["summary"]
    assert db.query(Task).filter(Task.id == 2).first().strategy == "NEW FOR TWO"
    # The active task must be untouched.
    assert db.query(Task).filter(Task.id == 1).first().strategy == "ORIGINAL ONE"
    db.close()


def test_task_chat_goes_through_the_same_tools_as_the_agent_chat():
    """Both chats edit one entity, so both must write through agent_tools.

    The task chat used to be a bare call_llm: "установи стратегию" there
    described a new strategy and saved nothing, while the same words in
    «Чат с агентом» really updated the record. app.py is a Streamlit script and
    cannot be imported here, so the wiring is asserted at source level.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def task_chat_reply("):]
    body = body[:body.index("\ndef ", 1)]

    assert "agent_tools.select_action" in body, "task chat no longer selects a tool"
    assert "agent_tools.run_action" in body, "task chat no longer executes the tool"
    assert "task.id" in body, "the write must target the task on screen"
