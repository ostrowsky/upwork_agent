"""Selector canary — detect Upwork DOM drift before it breaks production.

Opens the authenticated session, loads the feed (and optionally an apply page),
and checks that each critical selector from upwork_selectors still matches at
least one element. Prints a PASS/BROKEN report and exits non-zero if anything
is broken — suitable for a cron/alert.

Usage:
  python healthcheck.py                 # feed selectors only
  python healthcheck.py --apply <job_id>  # also check the apply form
"""
from __future__ import annotations

import sys

import upwork_selectors as S


def check_selectors(page, checks: dict[str, str]) -> dict[str, bool]:
    """For each {name: selector}, True if the page has ≥1 matching element.

    Pure w.r.t. the page object → unit-testable with a fake page.
    """
    result = {}
    for name, selector in checks.items():
        try:
            result[name] = page.locator(selector).count() > 0
        except Exception:  # noqa: BLE001
            result[name] = False
    return result


FEED_CHECKS = {
    "feed.tile": ", ".join(S.FEED_TILE_SELECTORS),
    "feed.title_link": S.FEED_TITLE_LINK,
    "feed.description": S.FEED_DESCRIPTION,
    "feed.budget": S.FEED_BUDGET,
}

APPLY_CHECKS = {
    "apply.cover_letter": S.APPLY_COVER_LETTER,
    "apply.rate_hourly": S.APPLY_RATE_HOURLY,
    "apply.rate_increase_freq": S.apply_rate_increase_toggle("How often"),
    "apply.send_button": S.APPLY_SEND_BUTTON,
}


def format_report(results: dict[str, bool]) -> str:
    lines = []
    for name, ok in results.items():
        lines.append(f"  [{'PASS' if ok else 'BROKEN'}] {name}")
    broken = [n for n, ok in results.items() if not ok]
    status = "OK" if not broken else f"BROKEN ({len(broken)})"
    return f"Selector healthcheck: {status}\n" + "\n".join(lines)


def run(apply_job_id: str | None = None) -> int:
    from upwork_connect import is_logged_in

    try:
        from browser import browser_page
    except ImportError:
        print("playwright not installed")
        return 2

    results: dict[str, bool] = {}
    with browser_page() as page:
        page.goto(S.BEST_MATCHES_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        authed, why = is_logged_in(page)
        if not authed:
            print(f"NOT AUTHENTICATED: {why}")
            return 2
        for sel in S.FEED_TILE_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=6000)
                break
            except Exception:  # noqa: BLE001
                continue
        results.update(check_selectors(page, FEED_CHECKS))

        if apply_job_id:
            jid = apply_job_id if apply_job_id.startswith("~") else "~" + apply_job_id
            page.goto(S.APPLY_URL_TEMPLATE.format(job_id=jid),
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
            results.update(check_selectors(page, APPLY_CHECKS))

    print(format_report(results))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    job_id = None
    if "--apply" in sys.argv:
        i = sys.argv.index("--apply")
        if i + 1 < len(sys.argv):
            job_id = sys.argv[i + 1]
    sys.exit(run(job_id))
