"""Tech-debt #5 — selector canary report logic (no browser)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import healthcheck  # noqa: E402


class _Loc:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _Page:
    def __init__(self, present):
        self._present = present  # set of selectors that "match"

    def locator(self, sel):
        return _Loc(1 if sel in self._present else 0)


def test_check_selectors_detects_present_and_missing():
    checks = {"a": "sel-a", "b": "sel-b", "c": "sel-c"}
    page = _Page(present={"sel-a", "sel-c"})
    res = healthcheck.check_selectors(page, checks)
    assert res == {"a": True, "b": False, "c": True}


def test_check_selectors_handles_locator_error():
    class _Boom:
        def locator(self, sel):
            raise RuntimeError("bad selector")

    res = healthcheck.check_selectors(_Boom(), {"x": "sel"})
    assert res == {"x": False}


def test_format_report_ok_and_broken():
    ok = healthcheck.format_report({"feed.tile": True, "feed.budget": True})
    assert "OK" in ok and "BROKEN" not in ok

    bad = healthcheck.format_report({"feed.tile": True, "feed.budget": False})
    assert "BROKEN (1)" in bad
    assert "[BROKEN] feed.budget" in bad
    assert "[PASS] feed.tile" in bad
