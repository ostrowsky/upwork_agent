"""Connects balance tracking (gap #4).

The account's connects balance is reliably shown on the apply page
("N Connects available" / "...have N Connects remaining"). We parse it there
(during submit, a page we already open) and persist it, instead of scraping a
separate connects page whose data is lazy-loaded.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

BALANCE_PATH = Path(__file__).resolve().parent / "data" / "connects_balance.json"


_REMAINING_PATTERNS = (
    r"have\s+(\d+)\s+Connects remaining",
    r"Remaining balance:\s*(\d+)\s+Connects",
)
_AVAILABLE_PATTERNS = (r"(\d+)\s+Connects available",)


def parse_connects_balance(text: str | None, prefer: str = "available") -> int | None:
    """Extract the connects balance from apply-page text.

    The apply form shows BOTH the current balance ("N Connects available") and
    the post-submit balance ("you'll have N Connects remaining"). `prefer`
    picks which to return first: "remaining" (new balance after this submit) or
    "available" (current balance). Falls back to the other if absent.
    """
    if not text:
        return None
    order = (
        _REMAINING_PATTERNS + _AVAILABLE_PATTERNS
        if prefer == "remaining"
        else _AVAILABLE_PATTERNS + _REMAINING_PATTERNS
    )
    for pat in order:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def save_balance(balance: int) -> dict:
    BALANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"balance": int(balance), "updated_at": datetime.now(timezone.utc).isoformat()}
    BALANCE_PATH.write_text(json.dumps(data), encoding="utf-8")
    return data


def read_balance() -> dict | None:
    if not BALANCE_PATH.exists():
        return None
    try:
        return json.loads(BALANCE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
