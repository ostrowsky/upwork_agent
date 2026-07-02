"""SPA apply navigation: route building, enable flag, fake-page fallback."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import submit  # noqa: E402


class _Loc:
    def __init__(self, n):
        self._n = n

    @property
    def first(self):
        return self

    def count(self):
        return self._n


class FakePage:
    """Minimal page: no get_by_role → SPA path bails, deep-link fallback used."""

    def __init__(self, title="Upwork", form=True):
        self._title = title
        self._form = form
        self.goto_calls = []
        self.url = "https://www.upwork.com/nx/proposals/job/~1/apply/"

    def goto(self, url, **k):
        self.goto_calls.append(url)

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return self._title

    def locator(self, sel):
        return _Loc(1 if self._form else 0)


class FakeJob:
    def __init__(self, upwork_job_id="022069750705030808080", jid=7):
        self.upwork_job_id = upwork_job_id
        self.id = jid


def test_apply_via_spa_enabled_default_and_toggle(monkeypatch):
    monkeypatch.delenv("APPLY_VIA_SPA", raising=False)
    assert submit.apply_via_spa_enabled() is True
    monkeypatch.setenv("APPLY_VIA_SPA", "0")
    assert submit.apply_via_spa_enabled() is False


def test_spa_job_route():
    assert submit._spa_job_route(FakeJob("022069")) == "/best-matches/details/~022069"
    # leading ~ stripped, not doubled
    assert submit._spa_job_route(FakeJob("~022069")) == "/best-matches/details/~022069"
    assert submit._spa_job_route(FakeJob("")) is None


def test_navigate_spa_bails_on_fake_page():
    # FakePage has no get_by_role → SPA nav must return False (so caller falls back)
    assert submit._navigate_apply_spa(FakePage(), FakeJob()) is False


def test_open_apply_page_falls_back_to_deeplink(monkeypatch):
    monkeypatch.delenv("APPLY_VIA_SPA", raising=False)  # SPA on, but fake page bails
    page = FakePage(title="Upwork", form=True)
    ok = submit._open_apply_page(page, "APPLY_URL", FakeJob())
    assert ok is True
    assert page.goto_calls == ["APPLY_URL"]  # only the deep-link goto ran


def test_open_apply_page_spa_disabled_goes_straight_to_goto(monkeypatch):
    monkeypatch.setenv("APPLY_VIA_SPA", "0")
    page = FakePage(title="Upwork", form=True)
    ok = submit._open_apply_page(page, "APPLY_URL", FakeJob())
    assert ok is True
    assert page.goto_calls == ["APPLY_URL"]


def test_open_apply_page_reports_cloudflare_block(monkeypatch):
    monkeypatch.setenv("APPLY_VIA_SPA", "0")
    page = FakePage(title="Just a moment...", form=False)  # stuck on challenge
    assert submit._open_apply_page(page, "APPLY_URL", FakeJob()) is False
