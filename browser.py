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


def _open_context_with_heal(p, profile_dir, headless):
    """Open the persistent context; on failure self-heal a wedged profile and retry once.

    A force-killed previous run can leave a zombie browser holding the profile or
    a stale singleton lock, making launch hang until timeout. We catch that, clean
    up (scoped to THIS profile, never the user's personal browser), and retry.
    """
    import time

    from upwork_connect import open_context

    try:
        return open_context(p, profile_dir, headless=headless)
    except Exception as first:  # noqa: BLE001 — typically a launch Timeout/TargetClosed on a locked profile
        # Step 1: profile-scoped self-heal (kill the profile process tree + stale locks).
        try:
            from browser_cleanup import cleanup_profile

            cleanup_profile(profile_dir)
            time.sleep(2.5)  # let the OS release the profile
        except Exception:  # noqa: BLE001
            pass
        try:
            return open_context(p, profile_dir, headless=headless)
        except Exception:  # noqa: BLE001
            pass
        # Step 2: nuclear — Edge background mode respawns a profile holder that a
        # scoped kill can't outrun; kill ALL Edge (matches the operator workflow).
        try:
            from browser_cleanup import kill_all_browsers, kill_all_on_stuck_enabled

            if kill_all_on_stuck_enabled():
                kill_all_browsers()
                time.sleep(2.5)
                return open_context(p, profile_dir, headless=headless)
        except Exception:  # noqa: BLE001
            pass
        raise first


@contextmanager
def browser_page():
    """Yield an authenticated Upwork page; the context is always closed.

    Self-heals a wedged Edge/Chrome profile (zombie process / stale lock) and
    retries once. Raises ImportError if Playwright is missing, or re-raises the
    original launch error if the retry also fails.
    """
    from playwright.sync_api import sync_playwright
    from upwork_connect import pick_profile_dir, headless_mode

    with sync_playwright() as p:
        context = _open_context_with_heal(p, pick_profile_dir(), headless_mode())
        try:
            page = context.pages[0] if context.pages else context.new_page()
            yield page
        finally:
            context.close()
