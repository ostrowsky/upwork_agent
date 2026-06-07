"""Tech-debt #1 — per-job bid computation (was hardcoded $35)."""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import submit  # noqa: E402


def _job(budget):
    return types.SimpleNamespace(title="t", budget=budget)


@pytest.fixture(autouse=True)
def _clear_overrides(monkeypatch):
    monkeypatch.delenv("BID_HOURLY_RATE", raising=False)
    monkeypatch.delenv("BID_FIXED_AMOUNT", raising=False)
    monkeypatch.delenv("BID_HOURLY_STRATEGY", raising=False)


def test_parse_money_with_k_suffix():
    assert submit.parse_money("Total: $6.5k-$9k") == [6500.0, 9000.0]
    assert submit.parse_money("Hourly: $15-$30") == [15.0, 30.0]
    assert submit.parse_money("Fixed-price") == []


def test_parse_hourly_bounds():
    assert submit.parse_hourly_bounds("Hourly: $15-$30") == (15.0, 30.0)
    assert submit.parse_hourly_bounds("Hourly: $4") == (4.0, 4.0)
    assert submit.parse_hourly_bounds("Hourly") is None
    assert submit.parse_hourly_bounds("Fixed-price") is None


def test_compute_hourly_strategy(monkeypatch):
    job = _job("Hourly: $15-$30")
    assert submit.compute_hourly_rate(job) == "30"  # default max
    monkeypatch.setenv("BID_HOURLY_STRATEGY", "min")
    assert submit.compute_hourly_rate(job) == "15"
    monkeypatch.setenv("BID_HOURLY_STRATEGY", "mid")
    assert submit.compute_hourly_rate(job) == "22.50"


def test_compute_hourly_override_wins(monkeypatch):
    monkeypatch.setenv("BID_HOURLY_RATE", "50")
    assert submit.compute_hourly_rate(_job("Hourly: $15-$30")) == "50"


def test_compute_hourly_no_bounds_returns_none():
    assert submit.compute_hourly_rate(_job("Hourly")) is None  # keep profile default


def test_compute_fixed_from_estimate():
    job = _job("Fixed-price")
    proposal = types.SimpleNamespace(estimate="Phase 1 $2k, Phase 2 $4k, Total: $6.5k")
    assert submit.compute_fixed_amount(job, proposal) == "6500"  # largest = total


def test_compute_fixed_override_wins(monkeypatch):
    monkeypatch.setenv("BID_FIXED_AMOUNT", "1234")
    job = _job("Fixed-price")
    proposal = types.SimpleNamespace(estimate="Total: $6.5k")
    assert submit.compute_fixed_amount(job, proposal) == "1234"


def test_compute_fixed_falls_back_to_budget():
    job = _job("Fixed-price: $800")
    proposal = types.SimpleNamespace(estimate=None)
    assert submit.compute_fixed_amount(job, proposal) == "800"
