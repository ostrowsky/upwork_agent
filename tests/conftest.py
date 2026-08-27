"""Test-suite safety net: no test may reach an external service.

worker.tick() sends a Telegram/Discord alert when it detects an anomaly, and
several worker tests drive tick() with a deliberately failing session probe.
With real credentials present in .env (dotenv loads them on import) those runs
delivered genuine "🚨 worker anomalies: session_down" messages to the operator's
chat — the test suite was spamming production notifications.

The block sits at the NETWORK boundary rather than on send_telegram/send_discord
themselves, so those functions keep running their real logic (a test asserting
they refuse an unconfigured channel still exercises the real guard) while an
actual delivery is impossible. Clearing the credentials is the first line; the
httpx tripwire catches any path that supplies its own.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _no_outbound_notifications(monkeypatch):
    # Without credentials the real senders return "not configured" and never
    # touch the network — this alone stops the leak.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")

    import httpx

    def _blocked(*args, **kwargs):
        raise AssertionError(
            f"test suite attempted an outbound HTTP call: {args[:1]} — "
            "mock it in the test instead of letting it reach the network"
        )

    monkeypatch.setattr(httpx, "post", _blocked)
