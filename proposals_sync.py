"""Sync submitted-proposal status FROM Upwork → keep the app in sync with reality.

The app can drift from Upwork (manual revert, a false 'not confirmed', a proposal
sent elsewhere). This reconciles by reading Upwork's submitted proposals and
marking matching jobs SENT.

Layers (same pattern as feed/messages):
- capture_proposals()  — probe: dump API/__NUXT__ to find the data source.
- parse_submitted()    — pure: proposal objects → {upwork_job_id, title}.
- sync_submitted()     — pure: match to Jobs by upwork_job_id, mark SENT.
- fetch_submitted()    — browser read (finalized against the captured source).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from database import get_db_session, Job, Proposal

PROPOSALS_URL = "https://www.upwork.com/nx/proposals/"
ARCHIVED_URL = "https://www.upwork.com/nx/proposals/archived"
CONNECTS_URL = "https://www.upwork.com/nx/plans/connects/history/"

# Candidate keys that may carry the proposal/job status text on the archived page.
_STATUS_KEYS = ("status", "statusText", "statusLabel", "subStatus", "reason",
                "archivedReason", "jobStatus", "state", "label")


def _extract_job_id(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"~([0-9a-fA-F]{16,})", text)
    return m.group(1) if m else None


def parse_submitted(raw) -> list[dict]:
    """Map raw proposal/job objects → {upwork_job_id, title}. Tolerant of shapes."""
    records = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        cipher = (
            item.get("ciphertext")
            or item.get("jobCiphertext")
            or (item.get("job") or {}).get("ciphertext")
            or _extract_job_id(item.get("jobUrl") or item.get("url"))
        )
        jid = (cipher or "").lstrip("~") or None
        title = (item.get("title") or (item.get("job") or {}).get("title") or "").strip()
        if jid or title:
            records.append({"upwork_job_id": jid, "title": title})
    return records


def sync_submitted(records, db) -> dict:
    """Mark our Jobs SENT where Upwork shows a submitted proposal.

    Match by upwork_job_id (preferred), else exact title. Idempotent: a job
    already SENT is left untouched. Returns {matched, marked, unmatched}.
    """
    marked = matched = unmatched = 0
    for rec in records or []:
        job = None
        if rec.get("upwork_job_id"):
            job = db.query(Job).filter(Job.upwork_job_id == rec["upwork_job_id"]).first()
        if job is None and rec.get("title"):
            job = db.query(Job).filter(Job.title == rec["title"]).first()
        if job is None:
            unmatched += 1
            continue
        matched += 1
        if job.status == "SENT":
            continue
        job.status = "SENT"
        prop = (
            db.query(Proposal)
            .filter(Proposal.job_id == job.id)
            .order_by(Proposal.id.desc())
            .first()
        )
        if prop and prop.status != "SENT":
            prop.status = "SENT"
            if prop.submitted_at is None:
                prop.submitted_at = datetime.now(timezone.utc)
        marked += 1
    db.commit()
    return {"matched": matched, "marked": marked, "unmatched": unmatched}


# --------------------------------------------------------------------------
# Outcome auto-tracking (gap #5): won / lost / closed from the archived page.
# --------------------------------------------------------------------------

def _status_text(item: dict) -> str:
    """Collect every plausible status string from a raw proposal object (tolerant)."""
    parts = []
    for src in (item, item.get("job") if isinstance(item.get("job"), dict) else {}):
        for k in _STATUS_KEYS:
            v = (src or {}).get(k)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
            elif isinstance(v, dict):  # e.g. {"label": "Declined"}
                for vv in v.values():
                    if isinstance(vv, str) and vv.strip():
                        parts.append(vv.strip())
    return " | ".join(parts)


def classify_outcome(status_text: str | None) -> tuple[str | None, str]:
    """Map Upwork's archived-proposal status text → our outcome.

    Returns (outcome, subreason): ("WIN", "hired") / ("LOST", "declined") /
    ("LOST", "job closed") / (None, "") when nothing actionable (e.g. we
    withdrew the proposal ourselves, or the status is unknown).
    """
    t = (status_text or "").lower()
    if not t:
        return None, ""
    if re.search(r"\bhired\b|offer accepted|you('| a)re hired", t):
        return "WIN", "hired"
    if re.search(r"declined|rejected|not selected|client passed", t):
        return "LOST", "declined"
    if re.search(r"\bclosed\b|expired|cancell?ed|no longer (available|accepting)|position filled|job is closed", t):
        return "LOST", "job closed"
    return None, ""


def parse_archived(raw) -> list[dict]:
    """Raw archived-proposal objects → {upwork_job_id, title, status_text}."""
    records = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        cipher = (
            item.get("ciphertext")
            or item.get("jobCiphertext")
            or (item.get("job") or {}).get("ciphertext")
            or _extract_job_id(item.get("jobUrl") or item.get("url"))
        )
        jid = (cipher or "").lstrip("~") or None
        title = (item.get("title") or (item.get("job") or {}).get("title") or "").strip()
        if jid or title:
            records.append({"upwork_job_id": jid, "title": title,
                            "status_text": _status_text(item)})
    return records


def sync_outcomes(records, db, llm=None) -> dict:
    """Set Job.outcome from archived-proposal records (idempotent, SENT-only).

    hired → WIN; declined → LOST with LLM post-mortem (feeds learning);
    job closed → LOST with a fixed loss_reason, NO LLM (nothing to learn from a
    job nobody won). Jobs with an outcome already set are never overwritten.
    Returns {matched, win, lost_declined, lost_closed, skipped, unmatched}.
    """
    import learning

    win = lost_declined = lost_closed = skipped = matched = unmatched = 0
    for rec in records or []:
        job = None
        if rec.get("upwork_job_id"):
            job = db.query(Job).filter(Job.upwork_job_id == rec["upwork_job_id"]).first()
        if job is None and rec.get("title"):
            job = db.query(Job).filter(Job.title == rec["title"]).first()
        if job is None:
            unmatched += 1
            continue
        matched += 1
        outcome, sub = classify_outcome(rec.get("status_text"))
        if outcome is None or job.outcome or job.status != "SENT":
            skipped += 1
            continue
        if outcome == "WIN":
            learning.set_outcome(db, job.id, "WIN", llm=llm)
            win += 1
        elif sub == "declined":
            learning.set_outcome(db, job.id, "LOST", llm=llm)  # LLM post-mortem → learning
            lost_declined += 1
        else:  # job closed without hire — record, skip the post-mortem
            job.outcome = "LOST"
            job.loss_reason = "Заказ закрыт без найма (auto-sync с Upwork)"
            db.commit()
            lost_closed += 1
    return {"matched": matched, "win": win, "lost_declined": lost_declined,
            "lost_closed": lost_closed, "skipped": skipped, "unmatched": unmatched}


# DOM fallback: the archived list renders client-side (no NUXT array, no
# proposal XHR we can rely on) — scrape proposal cards (title link + card text,
# which contains the status, e.g. "Job is closed").
_DOM_CARDS_JS = """
() => {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('a[href*="/nx/proposals/"], a[href*="/jobs/"]').forEach(a => {
    const href = a.getAttribute('href') || '';
    if (href.includes('/archived') || href.includes('/submitted')) return;
    if (a.closest('nav, header, [role="navigation"]')) return;  // skip site nav links
    const t = (a.innerText || '').trim();
    if (!t || t.length < 5 || seen.has(t)) return;
    seen.add(t);
    let card = a;
    for (let i = 0; i < 5 && card.parentElement; i++) {
      card = card.parentElement;
      if ((card.innerText || '').length > t.length + 15) break;
    }
    out.push({title: t, status: (card.innerText || '').slice(0, 400), url: href});
  });
  return out;
}
"""


def _fetch_proposal_objects(url: str, dom_fallback: bool = False) -> list[dict]:
    """Read proposal objects from an Upwork proposals page (NUXT → API → DOM)."""
    from browser import browser_page
    from jobs import _find_job_arrays

    captured = {"api": []}

    def on_response(resp):
        try:
            u = resp.url.lower()
            if "proposal" in u and "json" in (resp.headers.get("content-type") or "").lower():
                captured["api"].append(resp.json())
        except Exception:  # noqa: BLE001
            pass

    with browser_page() as page:
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)
        state = page.evaluate("() => window.__NUXT__ ? window.__NUXT__.state : null")
        arrays = _find_job_arrays(state) if state else []
        arrays.sort(key=lambda x: -x[1])
        if arrays:
            node = state
            for key, idx in re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", arrays[0][0]):
                node = node[key] if key else node[int(idx)]
            if isinstance(node, list) and node:
                return node
        # fallback 1: flatten captured API payloads looking for lists
        for payload in captured["api"]:
            for v in (payload.values() if isinstance(payload, dict) else []):
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
        # fallback 2: scrape the rendered cards (client-side lists, e.g. archived)
        if dom_fallback:
            try:
                return page.evaluate(_DOM_CARDS_JS) or []
            except Exception:  # noqa: BLE001
                return []
        return []


def fetch_submitted() -> list[dict]:
    """Read submitted proposals from Upwork via the session."""
    return _fetch_proposal_objects(PROPOSALS_URL)


def fetch_archived() -> list[dict]:
    """Read archived proposals (won/lost/closed live here) via the session."""
    return _fetch_proposal_objects(ARCHIVED_URL, dom_fallback=True)


def sync_outcomes_from_upwork(db=None, llm=None) -> dict:
    """Browser read of the archived proposals → auto-set outcomes. Safe wrapper."""
    owns = db is None
    db = db or get_db_session()
    try:
        records = parse_archived(fetch_archived())
        result = sync_outcomes(records, db, llm=llm)
        result["ok"] = True
        result["found"] = len(records)
        return result
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}", "matched": 0,
                "win": 0, "lost_declined": 0, "lost_closed": 0}
    finally:
        if owns:
            db.close()


def sync_from_upwork(db=None) -> dict:
    owns = db is None
    db = db or get_db_session()
    try:
        records = parse_submitted(fetch_submitted())
        result = sync_submitted(records, db)
        result["ok"] = True
        result["found"] = len(records)
        return result
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}", "matched": 0, "marked": 0}
    finally:
        if owns:
            db.close()


def capture_for_sync() -> dict:
    """One probe for gaps #4 (connects balance) + #5 (proposal stage/offers).

    Visits the proposals page (offers/timeline) and the connects page (balance),
    dumping XHR JSON + __NUXT__ state so parsers can be written from real payloads.
    Writes data/sync_{proposals,connects}_*.json and *_state.json.
    """
    import json as _json
    from pathlib import Path
    from browser import browser_page

    data_dir = Path(__file__).resolve().parent / "data"
    out = {"proposals_files": [], "connects_files": []}

    def dumper(prefix, bucket, kw):
        def on_response(resp):
            try:
                url = resp.url.lower()
                if "json" not in (resp.headers.get("content-type") or "").lower():
                    return
                if not any(k in url for k in kw):
                    return
                fp = data_dir / f"{prefix}_{len(out[bucket]):02d}.json"
                fp.write_text(_json.dumps({"url": resp.url, "body": resp.json()},
                                          ensure_ascii=False, indent=2)[:2_000_000], encoding="utf-8")
                out[bucket].append(fp.name)
            except Exception:  # noqa: BLE001
                pass
        return on_response

    with browser_page() as page:
        # Proposals (stage / offers / submitted)
        page.on("response", dumper("sync_proposals", "proposals_files",
                                   ("proposal", "offer", "/api/", "graphql")))
        page.goto(PROPOSALS_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        page.reload(wait_until="domcontentloaded")  # force list XHR (cache-served otherwise)
        page.wait_for_timeout(7000)
        st = page.evaluate("() => { const s=window.__NUXT__&&window.__NUXT__.state; return s?Object.keys(s):null; }")
        # The real data is in the SSR state subtrees, not XHR — dump them in full.
        subtree = page.evaluate(
            "() => { const s=(window.__NUXT__&&window.__NUXT__.state)||{}; "
            "return {connects: s.connects, interview: s.interview, "
            "proposalDetails: s['proposal-details'], jobApply: s['job-apply']}; }"
        )
        (data_dir / "sync_proposals_state.json").write_text(
            _json.dumps({"state_keys": st, "subtree": subtree}, ensure_ascii=False, indent=2)[:3_000_000],
            encoding="utf-8")

        # Connects balance / history
        page.on("response", dumper("sync_connects", "connects_files",
                                   ("connect", "balance", "/api/", "graphql")))
        page.goto(CONNECTS_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(9000)
        st2 = page.evaluate("() => { const s=window.__NUXT__&&window.__NUXT__.state; return s?Object.keys(s):null; }")
        (data_dir / "sync_connects_state.json").write_text(_json.dumps({"state_keys": st2}), encoding="utf-8")

        return {"ok": True, **out, "proposals_state_keys": st, "connects_state_keys": st2}


def capture_proposals() -> dict:
    """Probe: dump API/__NUXT__ from the proposals page to find the data source."""
    import json as _json
    from pathlib import Path
    from browser import browser_page

    data_dir = Path(__file__).resolve().parent / "data"
    files = []
    with browser_page() as page:
        def on_response(resp):
            try:
                url = resp.url.lower()
                if "json" in (resp.headers.get("content-type") or "").lower() and (
                    "proposal" in url or "/api/" in url or "graphql" in url
                ):
                    fp = data_dir / f"prop_api_{len(files):02d}.json"
                    fp.write_text(_json.dumps({"url": resp.url, "body": resp.json()},
                                              ensure_ascii=False, indent=2)[:2_000_000], encoding="utf-8")
                    files.append(fp.name)
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)
        page.goto(PROPOSALS_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(9000)
        state = page.evaluate("() => { const s=window.__NUXT__&&window.__NUXT__.state; return s?Object.keys(s):null; }")
        return {"ok": True, "files": files, "state_keys": state}
