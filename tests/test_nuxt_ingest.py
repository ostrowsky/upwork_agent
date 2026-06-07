"""Tech-debt #2 — network-style ingest from window.__NUXT__ (stable, no DOM)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs  # noqa: E402


def test_budget_hourly_range():
    j = {"type": 2, "hourlyBudget": {"type": "Manual", "min": 15, "max": 30}}
    assert jobs._budget_from_nuxt(j) == "Hourly: $15-$30"


def test_budget_hourly_single_and_none():
    assert jobs._budget_from_nuxt({"type": 2, "hourlyBudget": {"min": 0, "max": 25}}) == "Hourly: $25"
    assert jobs._budget_from_nuxt({"type": 2, "hourlyBudget": {"type": "NotProvided", "min": 0, "max": 0}}) == "Hourly"


def test_budget_fixed():
    assert jobs._budget_from_nuxt({"type": 1, "amount": {"amount": 100}}) == "Fixed-price: $100"
    assert jobs._budget_from_nuxt({"type": 1, "amount": {"amount": 0}}) == "Fixed-price"


def test_parse_nuxt_jobs_maps_fields():
    raw = [
        {
            "ciphertext": "~022062591139000000",
            "title": "Unity Ludo Multiplayer",
            "description": "Build a multiplayer Ludo game",
            "type": 2,
            "hourlyBudget": {"type": "Manual", "min": 15, "max": 30},
        },
        {"ciphertext": "~02x", "title": "", "description": "no title"},  # skipped
    ]
    recs = jobs.parse_nuxt_jobs(raw)
    assert len(recs) == 1
    r = recs[0]
    assert r["title"] == "Unity Ludo Multiplayer"
    assert r["budget"] == "Hourly: $15-$30"
    assert r["upwork_job_id"] == "022062591139000000"
    assert r["source_url"] == "https://www.upwork.com/jobs/~022062591139000000/"


def test_parse_nuxt_jobs_handles_empty_and_garbage():
    assert jobs.parse_nuxt_jobs(None) == []
    assert jobs.parse_nuxt_jobs(["not a dict", 5]) == []


def test_array_at_best_path_finds_search_results():
    # Search page state: results nested under an arbitrary key.
    state = {
        "search": {"results": {"jobs": [
            {"ciphertext": "~021aaaaaaaaaaaaaaaa", "title": "Unity dev", "description": "build a game"},
            {"ciphertext": "~021bbbbbbbbbbbbbbbb", "title": "Unity dev 2", "description": "another"},
        ]}},
        "noise": {"items": [{"x": 1}]},
    }
    found = jobs._array_at_best_path(state)
    assert isinstance(found, list) and len(found) == 2
    recs = jobs.parse_nuxt_jobs(found)
    assert recs[0]["title"] == "Unity dev"


def test_array_at_best_path_none_when_absent():
    assert jobs._array_at_best_path({"a": {"b": [1, 2, 3]}}) is None


def test_parse_strips_search_highlight_html():
    raw = [{
        "ciphertext": "~021aaaaaaaaaaaaaaaa",
        "title": '<span class="highlight">Unity</span> Developer &amp; Designer',
        "description": "Build a <b>Unity</b> game",
        "type": 2, "hourlyBudget": {"min": 0, "max": 0},
    }]
    recs = jobs.parse_nuxt_jobs(raw)
    assert recs[0]["title"] == "Unity Developer & Designer"
    assert recs[0]["description"] == "Build a Unity game"
