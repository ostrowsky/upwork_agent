"""Day 3 — regression test for feed scrape selectors.

Loads a static HTML fixture mirroring the real Upwork best-matches markup
(verified 2026-06) and asserts the tile selectors still extract jobs. Skips
gracefully when no Chromium-family browser can launch (e.g. bare CI).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "feed_sample.html")


@pytest.fixture()
def page():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = (
                    p.chromium.launch(channel=channel, headless=True)
                    if channel
                    else p.chromium.launch(headless=True)
                )
                break
            except Exception:  # noqa: BLE001
                continue
        if browser is None:
            pytest.skip("no Chromium-family browser available")
        pg = browser.new_page()
        with open(FIXTURE, encoding="utf-8") as fh:
            pg.set_content(fh.read(), wait_until="domcontentloaded")
        yield pg
        browser.close()


def test_selectors_extract_jobs(page):
    _, count = jobs._first_tile_locator(page)
    assert count == 2

    recs = jobs._extract_tiles(page)
    assert len(recs) == 2

    first = recs[0]
    assert first["title"] == "Unity Multiplayer Developer"
    assert first["budget"] == "Fixed-price"
    assert "Photon" in first["description"]
    assert first["source_url"].startswith("https://www.upwork.com/jobs/")

    # upwork_job_id is derivable from the scraped URL.
    assert jobs.extract_upwork_job_id(first["source_url"]) == "021aaaaaaaaaaaaaaaaa"
