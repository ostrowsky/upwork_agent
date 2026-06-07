"""Job ingestion — shared by the worker (auto feed) and the UI (manual import).

Two layers, kept separate so the dedup logic is unit-testable without a browser:

- ``normalize_record`` / ``ingest_jobs`` — pure-ish DB logic, fully tested.
- ``scrape_best_matches`` — fragile Playwright scrape of the Upwork feed.

Qualification (APPLY/SKIP) is NOT here — that's Day 4. Ingest only sets NEW.
"""
from __future__ import annotations

import re
from typing import Iterable

from database import get_db_session, Job
import upwork_selectors as S


# Upwork job URLs look like /jobs/~021... or /nx/...?jobId=... ; pull a stable id.
_JOB_ID_RE = re.compile(r"~([0-9a-fA-F]{16,})|jobId=([0-9]+)")


def extract_upwork_job_id(source_url: str | None) -> str | None:
    if not source_url:
        return None
    m = _JOB_ID_RE.search(source_url)
    if not m:
        return None
    return m.group(1) or m.group(2)


def normalize_record(raw: dict) -> dict | None:
    """Clean one scraped/pasted record into the ingest shape.

    Requires at least a title and a description. Derives upwork_job_id from the
    URL when not provided. Returns None if the record is too empty to be a job.
    """
    title = (raw.get("title") or "").strip()
    description = (raw.get("description") or "").strip()
    if not title or not description:
        return None

    source_url = (raw.get("source_url") or "").strip() or None
    upwork_job_id = (raw.get("upwork_job_id") or "").strip() or extract_upwork_job_id(source_url)

    return {
        "title": title[:255],
        "description": description,
        "budget": (raw.get("budget") or "").strip() or None,
        "source_url": source_url,
        "upwork_job_id": upwork_job_id,
    }


def _is_duplicate(db, rec: dict) -> bool:
    """A job is a duplicate if its upwork_job_id or source_url already exists."""
    if rec.get("upwork_job_id"):
        if db.query(Job).filter(Job.upwork_job_id == rec["upwork_job_id"]).first():
            return True
    if rec.get("source_url"):
        if db.query(Job).filter(Job.source_url == rec["source_url"]).first():
            return True
    return False


def ingest_jobs(records: Iterable[dict], task_id: int | None = None, db=None) -> dict:
    """Insert new jobs as NEW, skipping duplicates. Idempotent on re-run.

    Returns {"added": int, "skipped_dup": int, "invalid": int}.
    Dedup also applies within the same batch (no double-insert of one feed).
    """
    owns_session = db is None
    db = db or get_db_session()
    added = skipped = invalid = 0
    seen_in_batch: set[str] = set()
    try:
        for raw in records:
            rec = normalize_record(raw)
            if rec is None:
                invalid += 1
                continue

            key = rec.get("upwork_job_id") or rec.get("source_url")
            if key and key in seen_in_batch:
                skipped += 1
                continue

            if _is_duplicate(db, rec):
                skipped += 1
                continue

            db.add(
                Job(
                    task_id=task_id,
                    title=rec["title"],
                    description=rec["description"],
                    budget=rec["budget"],
                    source_url=rec["source_url"],
                    upwork_job_id=rec["upwork_job_id"],
                    status="NEW",
                )
            )
            if key:
                seen_in_batch.add(key)
            added += 1
        db.commit()
    finally:
        if owns_session:
            db.close()

    return {"added": added, "skipped_dup": skipped, "invalid": invalid}


# --------------------------------------------------------------------------
# Fragile layer: scrape the authenticated Upwork "best matches" feed.
# Selectors drift; failures degrade to an empty list (worker logs the reason).
# --------------------------------------------------------------------------

BEST_MATCHES_URL = S.BEST_MATCHES_URL


# Selectors live in selectors.py (single source of truth for Upwork's DOM).
_TILE_SELECTORS = S.FEED_TILE_SELECTORS


def _first_tile_locator(page):
    """Return (locator, count) for the first tile selector that matches."""
    for sel in _TILE_SELECTORS:
        loc = page.locator(sel)
        try:
            if loc.count() > 0:
                return loc, loc.count()
        except Exception:  # noqa: BLE001
            continue
    return None, 0


def _extract_tiles(page) -> list[dict]:
    """Pull job records from a loaded best-matches page. Best-effort selectors."""
    records: list[dict] = []
    tiles, count = _first_tile_locator(page)
    if not tiles:
        return records

    for i in range(count):
        tile = tiles.nth(i)
        try:
            link = tile.locator(S.FEED_TITLE_LINK).first
            title = (link.inner_text(timeout=2000) or "").strip()
            href = link.get_attribute("href") or ""
            if href and href.startswith("/"):
                href = "https://www.upwork.com" + href
            desc_loc = tile.locator(S.FEED_DESCRIPTION).first
            description = (
                (desc_loc.inner_text(timeout=2000) or "").strip()
                if desc_loc.count()
                else title
            )
            budget_loc = tile.locator(S.FEED_BUDGET).first
            budget = (
                (budget_loc.inner_text(timeout=1500) or "").strip()
                if budget_loc.count()
                else ""
            )
        except Exception:  # noqa: BLE001 — skip a malformed tile, keep the rest
            continue
        if title:
            records.append(
                {"title": title, "description": description, "budget": budget, "source_url": href}
            )
    return records


def _dump_feed_debug(page) -> str:
    """Save page HTML + screenshot so selectors can be tuned against real DOM."""
    from pathlib import Path

    data_dir = Path(__file__).resolve().parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    html_path = data_dir / "feed_debug.html"
    png_path = data_dir / "feed_debug.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(png_path), full_page=True)
        return f"debug saved: {html_path.name}, {png_path.name}"
    except Exception as e:  # noqa: BLE001
        return f"debug dump failed: {e}"


def scrape_best_matches(limit: int = 20) -> dict:
    """Open the authenticated session and scrape the feed.

    Returns {"ok": bool, "reason": str, "records": list[dict]}.
    """
    from browser import browser_page
    from upwork_connect import is_logged_in

    try:
        with browser_page() as page:
            page.goto(BEST_MATCHES_URL, wait_until="domcontentloaded", timeout=60000)
            ok, reason = is_logged_in(page)
            if not ok:
                return {"ok": False, "reason": f"not authenticated: {reason}", "records": []}

            # SPA feed: wait for any tile selector, then scroll to trigger lazy load.
            for sel in _TILE_SELECTORS:
                try:
                    page.wait_for_selector(sel, timeout=8000)
                    break
                except Exception:  # noqa: BLE001
                    continue
            for _ in range(3):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1200)

            records = _extract_tiles(page)[:limit]
            reason_txt = f"{len(records)} tiles"
            if not records:
                reason_txt += " — " + _dump_feed_debug(page)
            return {"ok": True, "reason": reason_txt, "records": records}
    except ImportError:
        return {"ok": False, "reason": "playwright not installed", "records": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}", "records": []}


def capture_feed_api(out_dir: str | None = None) -> dict:
    """Capture the JSON API responses that hydrate the feed (tech-debt #2).

    DOM scraping is fragile; Upwork's SPA loads jobs from internal API/GraphQL
    calls whose JSON contract changes far less often. This records candidate
    JSON responses to data/feed_api_*.json so a network-based parser can be
    built against the real payloads. (Run once, then we write the parser.)
    """
    import json as _json
    from pathlib import Path
    from browser import browser_page

    data_dir = Path(out_dir) if out_dir else Path(__file__).resolve().parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    keywords = ("graphql", "/api/", "find-work", "best-matches", "jobtile", "search")

    with browser_page() as page:
        def on_response(resp):
            try:
                url = resp.url.lower()
                ctype = (resp.headers.get("content-type") or "").lower()
                if "json" not in ctype or not any(k in url for k in keywords):
                    return
                body = resp.json()
                idx = len(files)
                fp = data_dir / f"feed_api_{idx:02d}.json"
                fp.write_text(
                    _json.dumps({"url": resp.url, "body": body}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                files.append(fp.name)
            except Exception:  # noqa: BLE001 — capture is best-effort
                return

        page.on("response", on_response)
        page.goto(BEST_MATCHES_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(7000)
        for _ in range(3):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)
        return {"ok": True, "reason": f"captured {len(files)} JSON responses", "files": files}


def _find_job_arrays(node, path="$"):
    """Recursively find lists of dicts that look like job tiles in a nested object."""
    found = []
    if isinstance(node, list):
        if node and isinstance(node[0], dict):
            keys = set(node[0].keys())
            if {"ciphertext"} & keys or ({"title", "description"} <= keys):
                found.append((path, len(node), sorted(keys)[:15]))
        for i, item in enumerate(node[:50]):
            found += _find_job_arrays(item, f"{path}[{i}]")
    elif isinstance(node, dict):
        for k, v in node.items():
            found += _find_job_arrays(v, f"{path}.{k}")
    return found


def capture_nuxt_state() -> dict:
    """Evaluate window.__NUXT__ and locate the job array (tech-debt #2).

    The feed is SSR'd (no XHR API), but the hydrated state in window.__NUXT__
    holds structured jobs. Evaluating it in the browser returns a clean object,
    far more stable than CSS selectors. Dumps the found job array to
    data/nuxt_jobs.json so a network-style parser can be written.
    """
    import json as _json
    from pathlib import Path
    from browser import browser_page

    data_dir = Path(__file__).resolve().parent / "data"
    with browser_page() as page:
        page.goto(BEST_MATCHES_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)
        state = page.evaluate("() => window.__NUXT__ || null")
        if state is None:
            return {"ok": False, "reason": "window.__NUXT__ not present"}
        (data_dir / "nuxt_state.json").write_text(
            _json.dumps(state, ensure_ascii=False)[:5_000_000], encoding="utf-8"
        )
        arrays = _find_job_arrays(state)
        arrays.sort(key=lambda x: -x[1])
        best = arrays[0] if arrays else None
        if best:
            # re-extract the actual array at the discovered path for dumping
            node = state
            for part in re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", best[0]):
                key, idx = part
                node = node[key] if key else node[int(idx)]
            (data_dir / "nuxt_jobs.json").write_text(
                _json.dumps(node, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return {"ok": True, "candidates": arrays[:5], "best": best}


# --------------------------------------------------------------------------
# Preferred ingest path: structured jobs from window.__NUXT__ (tech-debt #2).
# Stable to markup changes — breaks only if the data schema changes.
# --------------------------------------------------------------------------

def _num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f.is_integer() else f"{f:.2f}"


def _budget_from_nuxt(job: dict) -> str:
    """Build a clean budget string from a NUXT job object."""
    if job.get("type") == 1:  # fixed-price
        amt = (job.get("amount") or {}).get("amount") or 0
        return f"Fixed-price: ${_num(amt)}" if amt else "Fixed-price"
    hb = job.get("hourlyBudget") or {}
    mn, mx = hb.get("min") or 0, hb.get("max") or 0
    if mx:
        return f"Hourly: ${_num(mn)}-${_num(mx)}" if mn and mn != mx else f"Hourly: ${_num(mx)}"
    return "Hourly"


def _clean_html(text: str | None) -> str:
    """Strip tags + unescape entities — search results wrap matches in
    <span class="highlight">…</span>, which must not leak into stored text."""
    import html as _html

    if not text:
        return ""
    return _html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def parse_nuxt_jobs(jobs_list) -> list[dict]:
    """Map window.__NUXT__ feed jobs → ingest records. Pure / unit-testable."""
    records = []
    for j in jobs_list or []:
        if not isinstance(j, dict):
            continue
        title = _clean_html(j.get("title"))
        desc = _clean_html(j.get("description"))
        if not title or not desc:
            continue
        cipher = (j.get("ciphertext") or "").strip()
        records.append({
            "title": title,
            "description": desc,
            "budget": _budget_from_nuxt(j),
            "source_url": f"https://www.upwork.com/jobs/{cipher}/" if cipher else None,
            "upwork_job_id": cipher.lstrip("~") or None,
        })
    return records


SEARCH_URL = "https://www.upwork.com/nx/search/jobs/"


def _array_at_best_path(state):
    """Return the largest job-like array found anywhere in a NUXT state object."""
    arrays = _find_job_arrays(state)
    if not arrays:
        return None
    arrays.sort(key=lambda x: -x[1])
    node = state
    for key, idx in re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", arrays[0][0]):
        node = node[key] if key else node[int(idx)]
    return node if isinstance(node, list) else None


def scrape_search(query: str, limit: int = 20) -> dict:
    """Keyword search ingest (gap #14) — beyond the profile-driven best-matches.

    The search page is also SSR'd into window.__NUXT__; we auto-locate the
    results array (same job shape as the feed). DOM tiles are the fallback.
    """
    from urllib.parse import quote_plus
    from browser import browser_page, wait_past_cloudflare
    from upwork_connect import is_logged_in

    url = f"{SEARCH_URL}?q={quote_plus(query)}"
    try:
        with browser_page() as page:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            wait_past_cloudflare(page)  # deep-link search trips a fresh CF challenge
            page.wait_for_timeout(2000)
            ok, reason = is_logged_in(page)
            if not ok and "search" not in page.url:
                return {"ok": False, "reason": f"not authenticated: {reason}", "records": []}

            state = page.evaluate("() => window.__NUXT__ ? window.__NUXT__.state : null")
            raw = _array_at_best_path(state) if state else None
            if raw:
                records = parse_nuxt_jobs(raw)[:limit]
                if records:
                    return {"ok": True, "reason": f"{len(records)} jobs (nuxt search)", "records": records}

            # DOM fallback
            for sel in _TILE_SELECTORS:
                try:
                    page.wait_for_selector(sel, timeout=8000)
                    break
                except Exception:  # noqa: BLE001
                    continue
            for _ in range(2):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1200)
            records = _extract_tiles(page)[:limit]
            reason_txt = f"{len(records)} tiles (search DOM)"
            if not records:
                reason_txt += " — " + _dump_feed_debug(page)
            return {"ok": True, "reason": reason_txt, "records": records}
    except ImportError:
        return {"ok": False, "reason": "playwright not installed", "records": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}", "records": []}


def ingest_search(query: str, task_id: int | None, limit: int = 20) -> dict:
    """Search Upwork by keyword and ingest the results (dedup-aware)."""
    scraped = scrape_search(query, limit=limit)
    if not scraped["ok"]:
        return {"ok": False, "reason": scraped["reason"], "added": 0, "skipped_dup": 0, "invalid": 0}
    result = ingest_jobs(scraped["records"], task_id=task_id)
    result["ok"] = True
    result["reason"] = scraped["reason"]
    return result


def scrape_best_matches_nuxt(limit: int = 20) -> dict:
    """Read the feed from window.__NUXT__ (no DOM selectors). Preferred path."""
    import upwork_selectors as S
    from browser import browser_page
    from upwork_connect import is_logged_in

    try:
        with browser_page() as page:
            page.goto(BEST_MATCHES_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            ok, reason = is_logged_in(page)
            if not ok:
                return {"ok": False, "reason": f"not authenticated: {reason}", "records": []}
            raw = page.evaluate(S.NUXT_FEED_JOBS_JS)
            if not raw:
                return {"ok": False, "reason": "no __NUXT__ feed", "records": []}
            records = parse_nuxt_jobs(raw)[:limit]
            return {"ok": True, "reason": f"{len(records)} jobs (nuxt)", "records": records}
    except ImportError:
        return {"ok": False, "reason": "playwright not installed", "records": []}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}", "records": []}


def ingest_from_upwork(task_id: int | None, limit: int = 20) -> dict:
    """Ingest the feed. Prefer the stable __NUXT__ path, fall back to DOM scrape."""
    scraped = scrape_best_matches_nuxt(limit=limit)
    if not scraped["ok"] or not scraped["records"]:
        scraped = scrape_best_matches(limit=limit)  # DOM fallback
    if not scraped["ok"]:
        return {"ok": False, "reason": scraped["reason"], "added": 0, "skipped_dup": 0, "invalid": 0}
    result = ingest_jobs(scraped["records"], task_id=task_id)
    result["ok"] = True
    result["reason"] = scraped["reason"]
    return result
