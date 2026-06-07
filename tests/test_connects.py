"""Gap #4 — connects balance parsing & persistence."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import connects  # noqa: E402


def test_parse_balance_variants():
    assert connects.parse_connects_balance("You have 72 Connects available now") == 72
    assert connects.parse_connects_balance("...you'll have 32 Connects remaining.") == 32
    assert connects.parse_connects_balance("Remaining balance: 48 Connects") == 48
    assert connects.parse_connects_balance("Send for 16 Connects") is None  # cost ≠ balance
    assert connects.parse_connects_balance("no connects here") is None
    assert connects.parse_connects_balance(None) is None


def test_parse_balance_prefers_remaining_or_available():
    # Apply form shows BOTH; available appears first in the DOM text.
    text = "72 Connects available ... you'll have 56 Connects remaining"
    assert connects.parse_connects_balance(text, prefer="available") == 72
    assert connects.parse_connects_balance(text, prefer="remaining") == 56
    # Falls back to the other when the preferred form is absent.
    assert connects.parse_connects_balance("40 Connects available", prefer="remaining") == 40


def test_save_and_read_balance(tmp_path, monkeypatch):
    monkeypatch.setattr(connects, "BALANCE_PATH", tmp_path / "connects_balance.json")
    assert connects.read_balance() is None
    saved = connects.save_balance(40)
    assert saved["balance"] == 40
    got = connects.read_balance()
    assert got["balance"] == 40 and "updated_at" in got
