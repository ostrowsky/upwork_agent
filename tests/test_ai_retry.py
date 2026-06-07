"""Gap #11/#12 — LLM retry/backoff and call counters."""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai  # noqa: E402


class _FakeCompletions:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("rate limit 429")
        msg = types.SimpleNamespace(content="ok-response")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


def _fake_client(fail_times):
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=_FakeCompletions(fail_times)))


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ai, "_sleep", lambda s: None)
    monkeypatch.setattr(ai, "LLM_MAX_RETRIES", 3)
    ai.LLM_STATS.update(calls=0, retries=0, errors=0)


def test_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr(ai, "client", _fake_client(fail_times=2))
    out = ai.call_llm([{"role": "user", "content": "hi"}])
    assert out == "ok-response"
    assert ai.LLM_STATS["calls"] == 1
    assert ai.LLM_STATS["retries"] == 2
    assert ai.LLM_STATS["errors"] == 0


def test_raises_after_max_retries(monkeypatch):
    monkeypatch.setattr(ai, "client", _fake_client(fail_times=99))
    with pytest.raises(RuntimeError):
        ai.call_llm([{"role": "user", "content": "hi"}])
    assert ai.LLM_STATS["errors"] == 1
    assert ai.LLM_STATS["retries"] == 2  # MAX_RETRIES-1 retries before giving up
