"""Gap #2 — message compose/send logic with a fake page (no real browser).

Locks in the three bugs we fixed live: clear-before-type, Shift+Enter for
newlines (not a send-triggering Enter), and post-send box-cleared verification.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import messages  # noqa: E402
import upwork_selectors as S  # noqa: E402


class FakeLocator:
    def __init__(self, count=1, text="", enabled=True, on_click=None):
        self._count = count
        self._text = text
        self._enabled = enabled
        self.on_click = on_click
        self.actions = []

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_enabled(self):
        return self._enabled

    def click(self, **k):
        self.actions.append("click")
        if self.on_click:
            self.on_click()

    def inner_text(self, **k):
        return self._text


class FakeKeyboard:
    def __init__(self):
        self.events = []

    def press(self, key):
        self.events.append(("press", key))

    def type(self, text, delay=None):
        self.events.append(("type", text))


class FakePage:
    def __init__(self, locators):
        self._locators = locators
        self.keyboard = FakeKeyboard()

    def locator(self, sel):
        return self._locators.get(sel, FakeLocator(count=0))

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        return "<html></html>"

    def screenshot(self, **k):
        pass


@pytest.fixture(autouse=True)
def _no_dump(monkeypatch):
    monkeypatch.setattr(messages, "_dump_compose", lambda page: None)


def test_dry_run_fills_clears_and_does_not_send():
    box = FakeLocator(count=1)
    page = FakePage({S.MSG_COMPOSE_INPUT: box, S.MSG_SEND_BUTTON: FakeLocator(count=1)})
    res = messages._compose_and_send(page, "Hello there", dry_run=True)
    assert res["dry_run"] is True and res["sent"] is False
    # cleared before typing
    assert ("press", "Control+a") in page.keyboard.events
    assert ("press", "Delete") in page.keyboard.events
    assert ("type", "Hello there") in page.keyboard.events


def test_newlines_use_shift_enter_not_send():
    page = FakePage({S.MSG_COMPOSE_INPUT: FakeLocator(count=1), S.MSG_SEND_BUTTON: FakeLocator(count=1)})
    messages._compose_and_send(page, "line one\n\nline two", dry_run=True)
    # one Shift+Enter per newline (2), and NO bare Enter while typing
    assert page.keyboard.events.count(("press", "Shift+Enter")) == 2
    assert ("press", "Enter") not in page.keyboard.events


def test_live_send_clicks_enabled_button_and_confirms():
    box = FakeLocator(count=1, text="")  # box empty after send → confirmed
    send = FakeLocator(count=1, enabled=True)
    page = FakePage({S.MSG_COMPOSE_INPUT: box, S.MSG_SEND_BUTTON: send})
    res = messages._compose_and_send(page, "Quick hello", dry_run=False)
    assert res["sent"] is True
    assert "click" in send.actions


def test_live_send_not_confirmed_when_box_still_has_text():
    box = FakeLocator(count=1, text="Quick hello that stayed")  # not cleared → failed
    send = FakeLocator(count=1, enabled=True)
    page = FakePage({S.MSG_COMPOSE_INPUT: box, S.MSG_SEND_BUTTON: send})
    res = messages._compose_and_send(page, "Quick hello that stayed", dry_run=False)
    assert res["sent"] is False
    assert "not confirmed" in res["reason"]


def test_compose_box_missing():
    page = FakePage({S.MSG_COMPOSE_INPUT: FakeLocator(count=0)})
    res = messages._compose_and_send(page, "hi", dry_run=False)
    assert res["sent"] is False and "compose box not found" in res["reason"]
