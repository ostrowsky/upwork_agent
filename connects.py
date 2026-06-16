"""Connects balance tracking (gap #4).

Two sources, both parsed and persisted:
- the apply page ("N Connects available" / "...have N remaining") — read for free
  during submit (parse_connects_balance), but can be stale/ambiguous;
- the dedicated Connects History page ("My balance: N Connects") — authoritative,
  read on demand via fetch_balance_live() (UI «🔄 Обновить баланс» / agent tool).
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

CONNECTS_URL = "https://www.upwork.com/nx/plans/connects/history/"
# The dedicated Connects History page shows "My balance\n163 Connects".
_BALANCE_PAGE_PATTERNS = (
    r"My balance[^\d]{0,40}(\d+)\s*Connects",
    r"balance[^\d]{0,20}(\d+)\s*Connects",
    r"(\d+)\s*Connects\b",
)


def parse_balance_page(text: str | None) -> int | None:
    """Extract the account balance from the Connects History page text."""
    if not text:
        return None
    for pat in _BALANCE_PAGE_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return None


def fetch_balance_live() -> dict:
    """Open the Connects History page, parse the real balance, persist it.

    Authoritative source ("My balance: N Connects"), unlike the apply-form value
    which can be stale. Returns {ok, balance, reason}.
    """
    try:
        from browser import browser_page
    except ImportError:
        return {"ok": False, "balance": None, "reason": "playwright missing"}
    try:
        with browser_page() as page:
            page.goto(CONNECTS_URL, wait_until="domcontentloaded", timeout=60000)
            bal = None
            for _ in range(8):  # SPA: balance hydrates a few seconds after load
                page.wait_for_timeout(1500)
                body = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                bal = parse_balance_page(body)
                if bal is not None:
                    break
            if bal is None:
                return {"ok": False, "balance": None, "reason": "balance not found on page"}
            save_balance(bal)
            return {"ok": True, "balance": bal, "reason": "ok"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "balance": None, "reason": f"{type(e).__name__}: {e}"}


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
