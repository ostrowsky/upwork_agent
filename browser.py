"""Shared browser-session helper — one place for the Playwright boilerplate.

Every browser operation needs the same setup: start Playwright, open the
authenticated persistent context, grab a page, and always close the context.
This was copy-pasted in ~11 places; now it lives here.

Locking (browser_lock) is intentionally NOT done here — callers manage it
(worker holds it across a whole tick; the UI wraps each action) so we don't
release a lock mid-operation.
"""
from __future__ import annotations

from contextlib import contextmanager

CLOUDFLARE_TITLE_MARKERS = ("момент", "moment", "challenge", "just a moment")


def wait_past_cloudflare(page, rounds: int = 16, step_ms: int = 1500) -> bool:
    """Wait out a Cloudflare 'Just a moment / Один момент' interstitial.

    Deep-linking pages (apply form, search) often trips a fresh challenge that a
    visible Edge on the real profile clears in a few seconds. Returns True once
    the page is no longer the challenge.
    """
    for _ in range(rounds):
        title = (page.title() or "").lower()
        if not any(m in title for m in CLOUDFLARE_TITLE_MARKERS):
            return True
        page.wait_for_timeout(step_ms)
    return not any(m in (page.title() or "").lower() for m in CLOUDFLARE_TITLE_MARKERS)


@contextmanager
def browser_page():
    """Yield an authenticated Upwork page; the context is always closed.

    Raises ImportError if Playwright is missing, or whatever open_context raises
    (e.g. profile locked) — callers handle those as they did before.
    """
    from playwright.sync_api import sync_playwright
    from upwork_connect import pick_profile_dir, open_context, headless_mode

    with sync_playwright() as p:
        context = open_context(p, pick_profile_dir(), headless=headless_mode())
        try:
            page = context.pages[0] if context.pages else context.new_page()
            yield page
        finally:
            context.close()
