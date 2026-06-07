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
