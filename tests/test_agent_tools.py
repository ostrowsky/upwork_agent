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
