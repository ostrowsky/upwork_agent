"""Proposal auto-submit — send a drafted proposal through the Upwork session.

SAFETY: submitting is outward-facing and irreversible (the client sees it and
connects are spent). Therefore:
- Dry-run is the DEFAULT. It navigates to the apply page and fills the cover
  letter but never clicks Send; it dumps apply_debug.html/png for selector tuning.
- A real submit requires AUTO_SUBMIT=1 in the environment (operator opt-in).
- Idempotent: a job already SENT is never re-submitted.
- A daily submit cap (DAILY_SUBMIT_LIMIT) guards against burning connects.

The pure guard/url logic is unit-tested; the browser part is isolated.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

from database import get_db_session, Job, Proposal
import upwork_selectors as S

# Load .env so AUTO_SUBMIT / SUBMIT_PER_RUN / BID_* are available regardless of
# import order (auto_submit_enabled is checked before the browser layer loads).
load_dotenv()


def auto_submit_enabled() -> bool:
    return os.getenv("AUTO_SUBMIT", "0").strip().lower() in ("1", "true", "yes")


def daily_submit_limit() -> int:
    return int(os.getenv("DAILY_SUBMIT_LIMIT", "10"))


def keep_browser_open() -> bool:
    """Hold the browser open until Enter — for manually inspecting the result."""
    return os.getenv("SUBMIT_KEEP_OPEN", "0").strip().lower() in ("1", "true", "yes")


def submit_per_run() -> int:
    """How many proposals to send per run (one button press / one worker tick).

    Default 1 — выпускаем по одному сабмиту за прогон; в проде расширим.
    """
    return int(os.getenv("SUBMIT_PER_RUN", "1"))


def bid_hourly_rate() -> str:
    """Explicit hourly override (empty → derive per-job from the budget)."""
    return os.getenv("BID_HOURLY_RATE", "").strip()


def bid_fixed_amount() -> str:
    """Explicit fixed override (empty → derive per-job from estimate/budget)."""
    return os.getenv("BID_FIXED_AMOUNT", "").strip()


def parse_money(text: str | None) -> list[float]:
    """Extract dollar amounts, expanding the 'k' suffix ($6.5k → 6500)."""
    if not text:
        return []
    out = []
    for num, suffix in re.findall(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?", text):
        try:
            val = float(num.replace(",", ""))  # comma = thousands separator ($20,000)
        except ValueError:
            continue
        out.append(val * 1000 if suffix else val)
    return out


def parse_hourly_bounds(budget: str | None) -> tuple[float, float] | None:
    """(min, max) hourly $ from a budget like 'Hourly: $15-$30'; None if absent."""
    if not budget or "fixed" in budget.lower():
        return None
    vals = [v for v in parse_money(budget) if v > 0]
    return (min(vals), max(vals)) if vals else None


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}"


def compute_hourly_rate(job: Job) -> str | None:
    """Per-job hourly bid. Priority: explicit override → budget bounds → None.

    The bid NEVER exceeds the client's stated hourly max — bidding above the
    posted range looks like we ignored the budget and hurts the proposal. So an
    explicit BID_HOURLY_RATE is capped to the job's upper bound when the job
    posts a range. Strategy for ranges (BID_HOURLY_STRATEGY): max (default) /
    mid / min. None means leave Upwork's pre-filled profile rate untouched.
    """
    bounds = parse_hourly_bounds(job.budget)
    override = bid_hourly_rate()
    if override:
        try:
            val = float(str(override).replace("$", "").replace(",", "").strip())
        except ValueError:
            return override  # non-numeric override → pass through verbatim
        if bounds:
            val = min(val, bounds[1])  # cap at the client's stated max
        return _fmt(val)
    if not bounds:
        return None
    lo, hi = bounds
    strategy = os.getenv("BID_HOURLY_STRATEGY", "max").strip().lower()
    val = lo if strategy == "min" else (lo + hi) / 2 if strategy == "mid" else hi
    return _fmt(val)


def compute_fixed_amount(job: Job, proposal: Proposal | None) -> str | None:
    """Per-job fixed bid. Priority: explicit override → estimate total → budget."""
    override = bid_fixed_amount()
    if override:
        return override
    # Prefer the largest amount in the estimate (usually the project total).
    est_vals = parse_money(getattr(proposal, "estimate", None)) if proposal else []
    if est_vals:
        return _fmt(max(est_vals))
    budget_vals = [v for v in parse_money(job.budget) if v > 0]
    return _fmt(max(budget_vals)) if budget_vals else None


def build_apply_url(job: Job) -> str | None:
    """Upwork apply page: /nx/proposals/job/~{id}/apply/. Needs the ~id."""
    jid = (job.upwork_job_id or "").strip()
    if not jid and job.source_url:
        m = re.search(r"~([0-9a-fA-F]{16,})", job.source_url)
        if m:
            jid = m.group(1)
    if not jid:
        return None
    if not jid.startswith("~"):
        jid = "~" + jid
    return S.APPLY_URL_TEMPLATE.format(job_id=jid)


def can_submit(job: Job, proposal: Proposal | None) -> tuple[bool, str]:
    """Idempotency / readiness guard. Returns (ok, reason)."""
    if proposal is None:
        return False, "no draft proposal"
    if proposal.status == "SENT" or job.status == "SENT":
        return False, "already sent"
    if proposal.status != "DRAFT":
        return False, f"proposal not DRAFT ({proposal.status})"
    if not (proposal.content or "").strip():
        return False, "empty content"
    if build_apply_url(job) is None:
        return False, "no apply url (missing job id)"
    return True, "ok"


def _start_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def submissions_today(db) -> int:
    return (
        db.query(Proposal)
        .filter(Proposal.submitted_at.isnot(None))
        .filter(Proposal.submitted_at >= _start_of_today_utc())
        .count()
    )


# --------------------------------------------------------------------------
# Browser layer — fragile; selectors tuned against the real apply page.
# --------------------------------------------------------------------------

def _wait_past_cloudflare(page, rounds: int = 16, step_ms: int = 1500) -> bool:
    """Wait out a Cloudflare 'Just a moment / Один момент' interstitial.

    A visible Edge on the real profile usually auto-clears managed challenges.
    Returns True once the page is no longer the challenge, else False on timeout.
    """
    markers = S.CLOUDFLARE_TITLE_MARKERS
    for _ in range(rounds):
        title = (page.title() or "").lower()
        if not any(m in title for m in markers):
            return True
        page.wait_for_timeout(step_ms)
    return not any(m in (page.title() or "").lower() for m in markers)


def _fill_bid(page, job: Job, proposal: Proposal | None = None) -> str | None:
    """Fill the required bid/rate field with a per-job value.

    Hourly → derived from the job's budget range (compute_hourly_rate);
    fixed → derived from the proposal estimate / budget (compute_fixed_amount).
    None means leave Upwork's pre-filled default. Returns the value filled.
    """
    budget = (job.budget or "").lower()

    def _set_currency(inp, value: str) -> str | None:
        # Masked currency fields don't clear on .fill(); select-all then type.
        if not inp.count():
            return None
        inp.click()
        inp.press("Control+a")
        inp.press("Delete")
        inp.type(value, delay=40)
        return value

    try:
        if "fixed" in budget:
            amount = compute_fixed_amount(job, proposal)
            if not amount:
                return None
            return _set_currency(page.locator(S.APPLY_RATE_FIXED).first, amount)
        rate = compute_hourly_rate(job)
        if not rate:
            return None  # keep profile default
        return _set_currency(page.locator(S.APPLY_RATE_HOURLY).first, rate)
    except Exception:  # noqa: BLE001
        return None


def _select_air3_dropdown(page, aria_contains: str, prefer_text: str = "") -> bool:
    """Open an air3 dropdown (aria-label on the parent .air3-dropdown) and pick an option.

    Picks the option whose text contains ``prefer_text`` if set, else the first
    real menu item. Verified markup: parent `.air3-dropdown[aria-label=...]`,
    toggle `[data-test="dropdown-toggle"]`, options `.air3-menu-item`.
    """
    toggle = page.locator(S.apply_rate_increase_toggle(aria_contains)).first
    if not toggle.count():
        toggle = page.locator(S.apply_rate_increase_toggle_fallback(aria_contains)).first
    if not toggle.count():
        return False
    try:
        toggle.scroll_into_view_if_needed(timeout=3000)
        toggle.click()
        page.wait_for_timeout(700)

        if prefer_text:
            chosen = page.locator(
                f'.air3-menu-item:visible:has-text("{prefer_text}"), '
                f'[role="option"]:visible:has-text("{prefer_text}")'
            ).first
            if chosen.count():
                chosen.click()
                page.wait_for_timeout(400)
                return True

        option = page.locator(S.APPLY_DROPDOWN_OPTION).first
        if option.count():
            option.click()
            page.wait_for_timeout(400)
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _fill_rate_increase(page) -> bool:
    """Fill the required 'Schedule a rate increase' dropdowns (frequency + percent).

    Upwork blocks submit until set despite the 'optional' label. The first
    frequency option is "Never", which satisfies the requirement AND hides the
    percent dropdown — so percent is only filled when its dropdown is present.
    Configurable via RATE_INCREASE_FREQUENCY / RATE_INCREASE_PERCENT (substring).
    """
    freq = _select_air3_dropdown(page, "How often", os.getenv("RATE_INCREASE_FREQUENCY", "").strip())
    page.wait_for_timeout(400)
    # Percent dropdown disappears when frequency == "Never"; fill only if present.
    pct_toggle = page.locator(S.APPLY_PERCENT_TOGGLE)
    if pct_toggle.count():
        _select_air3_dropdown(page, "How much", os.getenv("RATE_INCREASE_PERCENT", "").strip())
    return freq


def _dump_debug(page, name: str) -> str:
    from pathlib import Path

    data_dir = Path(__file__).resolve().parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        (data_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(data_dir / f"{name}.png"), full_page=True)
        return f"{name}.html/png saved"
    except Exception as e:  # noqa: BLE001
        return f"debug dump failed: {e}"


def _dump_apply_debug(page) -> str:
    return _dump_debug(page, "apply_debug")


def _dump_post_submit_debug(page) -> str:
    return _dump_debug(page, "post_submit_debug")


def _is_insufficient_connects(text: str | None) -> bool:
    """True if the apply page indicates the account lacks enough connects."""
    if not text:
        return False
    return bool(re.search(
        r"more Connects needed|insufficient connects|not enough Connects|Buy Connects|Get more Connects",
        text, re.IGNORECASE))


def _read_connects_required(page) -> int | None:
    """Cost of THIS proposal — read from the 'Send for N Connects' button.

    The page also shows the account balance ("72 Connects available"); we must
    not confuse it with the per-proposal cost, so we anchor on the Send button.
    """
    try:
        body = page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        return None
    m = re.search(S.CONNECTS_REGEX, body, re.IGNORECASE)
    return int(m.group(1)) if m else None


def attach_case_enabled() -> bool:
    """Attach generated case PDFs to the proposal (default on)."""
    return os.getenv("ATTACH_CASE_PDF", "1").strip().lower() in ("1", "true", "yes")


def attachment_paths_for_job(db, job_id) -> list[str]:
    """Existing PDF artifacts of synthetic cases generated for this job. Defensive."""
    if job_id is None:
        return []
    try:
        from database import CaseStudy

        rows = (
            db.query(CaseStudy)
            .filter(CaseStudy.job_id == job_id, CaseStudy.deleted == 0,
                    CaseStudy.artifact_path.isnot(None))
            .order_by(CaseStudy.created_at.desc())
            .all()
        )
        paths = []
        attach_png = os.getenv("ATTACH_CASE_PNG", "1").strip().lower() in ("1", "true", "yes")
        for r in rows:
            if r.artifact_path and os.path.exists(r.artifact_path):
                paths.append(r.artifact_path)
                # Attach the visual infographic (PNG) alongside the PDF, if present.
                png = os.path.splitext(r.artifact_path)[0] + ".png"
                if attach_png and os.path.exists(png):
                    paths.append(png)
        return paths
    except Exception:  # noqa: BLE001 — fake/unsupported db in tests, or query error
        return []


def _attach_files(page, paths: list[str]) -> dict:
    """Best-effort: upload files to the apply form's hidden file input + verify.

    Verification: after upload, the form lists the filename — we confirm each
    basename actually appears on the page, so we don't claim an attachment that
    silently failed. Never fatal (the submit proceeds regardless).
    """
    if not paths:
        return {"attached": 0, "verified": 0, "reason": "no files"}
    try:
        inp = page.locator(S.APPLY_ATTACH_INPUT).first
        if not inp.count():
            return {"attached": 0, "verified": 0, "reason": "no file input on form"}
        inp.set_input_files(paths)
        page.wait_for_timeout(2500)
        names = [os.path.basename(p) for p in paths]
        verified = 0
        try:
            body = page.inner_text("body", timeout=2500) or ""
            verified = sum(1 for n in names if n in body)
        except Exception:  # noqa: BLE001
            pass
        reason = "ok" if verified == len(paths) else "uploaded but filename not confirmed on page"
        return {"attached": len(paths), "verified": verified, "reason": reason}
    except Exception as e:  # noqa: BLE001 — attachment must not block the submit
        return {"attached": 0, "verified": 0, "reason": f"attach error: {e}"}


def _apply_on_page(page, url: str, job: Job, proposal: Proposal, db, dry_run: bool) -> dict:
    """Drive the apply form on an already-open page. Browser-agnostic, so it is
    unit-testable with a fake page object (see tests/test_submit_form.py)."""
    from upwork_connect import is_logged_in

    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    # Deep-linking the apply page can trigger a fresh Cloudflare challenge.
    if not _wait_past_cloudflare(page):
        _dump_apply_debug(page)
        return {"ok": False, "submitted": False, "dry_run": dry_run,
                "reason": "blocked by Cloudflare challenge (debug dumped)", "connects": None}

    authed, why = is_logged_in(page)
    if not authed and "apply" not in page.url:
        return {"ok": False, "submitted": False, "dry_run": dry_run,
                "reason": f"not authenticated: {why}", "connects": None}

    cover_sel = S.APPLY_COVER_LETTER
    try:
        page.wait_for_selector(cover_sel, timeout=10000)
    except Exception:  # noqa: BLE001
        pass
    # "Proposal settings" (rate/connects/Send) hydrates lazily and can lag.
    for sel in ('#step-rate', 'button:has-text("Send for")', 'input[data-test="currency-input"]'):
        try:
            page.wait_for_selector(sel, timeout=12000)
            break
        except Exception:  # noqa: BLE001
            continue
    page.wait_for_timeout(1500)

    cover = page.locator(cover_sel).first
    filled = False
    if cover.count():
        cover.fill(proposal.content)
        filled = True

    # Attach the generated case study PDF(s) for this job, if any (best-effort).
    attach = {"attached": 0, "reason": "disabled"}
    if attach_case_enabled():
        attach = _attach_files(page, attachment_paths_for_job(db, getattr(job, "id", None)))

    bid = _fill_bid(page, job, proposal)
    rate_increase = _fill_rate_increase(page)
    connects = _read_connects_required(page)
    # Capture connects balance text now (form shows both "N available" and the
    # post-submit "N remaining"). Save the CURRENT balance immediately; on a
    # confirmed live submit we override it with "remaining" (the new balance).
    balance_text = ""
    try:
        balance_text = page.inner_text("body", timeout=2000)
        from connects import parse_connects_balance, save_balance

        av = parse_connects_balance(balance_text, prefer="available")
        if av is not None:
            save_balance(av)
    except Exception:  # noqa: BLE001
        pass

    if dry_run:
        dbg = _dump_apply_debug(page)
        return {"ok": True, "submitted": False, "dry_run": True,
                "reason": (f"dry-run (filled={filled}, bid={bid}, rate_inc={rate_increase}, "
                           f"attached={attach['attached']}/verified={attach.get('verified', 0)}; {dbg})"),
                "connects": connects, "attached": attach["attached"],
                "attached_verified": attach.get("verified", 0)}

    # LIVE submit (primary button "Send for N Connects").
    send = page.locator(S.APPLY_SEND_BUTTON).first
    if not send.count():
        # No "Send for N Connects" button usually means insufficient connects —
        # Upwork hides it and shows a "More Connects Needed" flow. Report that
        # clearly (and DON'T submit) instead of a vague "button not found".
        if _is_insufficient_connects(balance_text):
            return {"ok": False, "submitted": False, "dry_run": False,
                    "reason": "insufficient connects — top up to submit (balance too low)",
                    "connects": connects}
        _dump_apply_debug(page)
        return {"ok": False, "submitted": False, "dry_run": False,
                "reason": "submit button not found (debug dumped)", "connects": connects}
    send.click()
    page.wait_for_timeout(3000)

    # Some flows show a final confirmation dialog — click it if present.
    try:
        confirm = page.locator(S.APPLY_CONFIRM_BUTTON).first
        if confirm.count():
            confirm.click()
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(5000)

    # Verify the submit went through. Upwork redirects to "My proposals" on
    # success → that's the positive signal. Only treat an error banner as failure
    # if we're STILL on the apply form (else a benign toast on the proposals page
    # caused false negatives → connects spent but SENT not recorded).
    post_url = page.url
    title = (page.title() or "").lower().strip()
    # NOTE: the apply URL itself is /nx/proposals/job/~id/apply/ — so a URL match
    # on "proposals" is NOT a success signal. Success = left the /apply/ form, or
    # the page title is the "My proposals" list ("Proposals", not "Submit a proposal").
    left_apply = "/apply" not in post_url
    on_proposals_list = title == "proposals" or "my proposals" in title
    succeeded = left_apply or on_proposals_list

    error_text = ""
    if not succeeded:  # still on the apply form → a real error blocked the send
        try:
            err = page.locator(S.APPLY_ERROR).first
            if err.count() and err.is_visible():
                error_text = (err.inner_text(timeout=1500) or "").strip()[:200]
        except Exception:  # noqa: BLE001
            pass
    _dump_post_submit_debug(page)

    if not succeeded or error_text:
        return {"ok": False, "submitted": False, "dry_run": False,
                "reason": f"not confirmed (url={post_url[:80]}; title={title[:30]}; "
                          f"error={error_text or 'n/a'}; post_submit_debug saved)", "connects": connects}

    proposal.status = "SENT"
    proposal.submitted_at = datetime.now(timezone.utc)
    proposal.connects_spent = connects
    job.status = "SENT"
    db.commit()

    # Submit confirmed → the new account balance is the "remaining" figure.
    try:
        from connects import parse_connects_balance, save_balance

        rem = parse_connects_balance(balance_text, prefer="remaining")
        if rem is not None:
            save_balance(rem)
    except Exception:  # noqa: BLE001
        pass

    return {"ok": True, "submitted": True, "dry_run": False,
            "reason": (f"sent (url={post_url[:80]}; "
                       f"attached={attach['attached']}/verified={attach.get('verified', 0)})"),
            "connects": connects, "attached": attach["attached"],
            "attached_verified": attach.get("verified", 0)}


def submit_many(items, db, dry_run: bool | None = None, progress=None,
                cap: int | None = None, already: int = 0,
                stop_after_insufficient: int | None = None) -> dict:
    """Submit a batch of (job, proposal) over ONE browser context (≈4× faster, fewer wedges).

    Reuses a single Edge session and navigates between apply forms instead of
    opening/closing the browser per job. Honors the daily cap (when not dry-run)
    and can stop early after N consecutive 'insufficient connects'.

    Returns {results: [per-job dicts], submitted, dry_run, errors, skipped_cap, total, last_reason}.
    """
    if dry_run is None:
        dry_run = not auto_submit_enabled()
    cap = daily_submit_limit() if cap is None else cap

    results = []
    ready = []
    for job, proposal in items:
        ok, reason = can_submit(job, proposal)
        if ok:
            ready.append((job, proposal))
        else:
            results.append({"job_id": getattr(job, "id", None), "ok": False, "submitted": False,
                            "dry_run": dry_run, "reason": reason, "connects": None})

    if ready:
        from browser import browser_page

        submitted_live = insufficient = 0
        try:
            with browser_page() as page:
                try:
                    for i, (job, proposal) in enumerate(ready, 1):
                        if not dry_run and already + submitted_live >= cap:
                            results.append({"job_id": job.id, "ok": False, "submitted": False,
                                            "dry_run": dry_run, "reason": "daily submit cap reached",
                                            "connects": None, "skipped_cap": True})
                            continue
                        res = _apply_on_page(page, build_apply_url(job), job, proposal, db, dry_run)
                        res.setdefault("job_id", job.id)
                        results.append(res)
                        if res.get("submitted"):
                            submitted_live += 1
                        if "insufficient connects" in (res.get("reason") or "").lower():
                            insufficient += 1
                            if stop_after_insufficient and insufficient >= stop_after_insufficient:
                                break
                        else:
                            insufficient = 0
                        if progress:
                            progress(i, len(ready), f"#{job.id} {(res.get('reason') or '')[:40]}")
                finally:
                    if keep_browser_open():
                        try:
                            input("[submit] Браузер открыт. Осмотри страницы, затем нажми Enter…")
                        except (EOFError, OSError):
                            pass
        except ImportError:
            results.append({"ok": False, "submitted": False, "dry_run": dry_run,
                            "reason": "playwright missing", "connects": None})
        except Exception as e:  # noqa: BLE001 — batch-level browser failure
            results.append({"ok": False, "submitted": False, "dry_run": dry_run,
                            "reason": f"{type(e).__name__}: {e}", "connects": None})

    submitted = sum(1 for r in results if r.get("submitted"))
    dry = sum(1 for r in results if r.get("dry_run") and r.get("ok"))
    skipped = sum(1 for r in results if r.get("skipped_cap"))
    errors = sum(1 for r in results
                 if not r.get("submitted") and not (r.get("dry_run") and r.get("ok"))
                 and not r.get("skipped_cap"))
    return {"results": results, "submitted": submitted, "dry_run": dry, "errors": errors,
            "skipped_cap": skipped, "total": len(items),
            "last_reason": results[-1].get("reason", "") if results else ""}


def submit_proposal(job: Job, proposal: Proposal, db, dry_run: bool | None = None) -> dict:
    """Submit a single proposal (thin wrapper over submit_many).

    Returns {ok, submitted, dry_run, reason, connects}. On a real send, marks
    proposal SENT (+ submitted_at, connects_spent) and job SENT.
    """
    r = submit_many([(job, proposal)], db, dry_run=dry_run)
    if r["results"]:
        return r["results"][0]
    return {"ok": False, "submitted": False,
            "dry_run": (not auto_submit_enabled()) if dry_run is None else dry_run,
            "reason": "no result", "connects": None}


def submit_ready(task_id: int | None, db=None, dry_run: bool | None = None,
                 limit: int | None = None, progress=None) -> dict:
    """Submit drafted proposals for PROPOSAL_DRAFTED jobs (one browser, daily cap)."""
    owns = db is None
    db = db or get_db_session()
    try:
        per_run = submit_per_run() if limit is None else limit
        q = db.query(Job).filter(Job.status == "PROPOSAL_DRAFTED")
        if task_id is not None:
            q = q.filter(Job.task_id == task_id)
        # Newest-first: fresh postings are still open; old ones often 302 to Proposals.
        jobs = q.order_by(Job.created_at.desc()).all()
        if per_run is not None and per_run >= 0:
            jobs = jobs[:per_run]

        items = []
        for job in jobs:
            proposal = (
                db.query(Proposal)
                .filter(Proposal.job_id == job.id, Proposal.status == "DRAFT")
                .first()
            )
            items.append((job, proposal))

        r = submit_many(items, db, dry_run=dry_run, progress=progress,
                        cap=daily_submit_limit(), already=submissions_today(db))
        return {"submitted": r["submitted"], "dry_run": r["dry_run"], "skipped_cap": r["skipped_cap"],
                "errors": r["errors"], "total": r["total"], "last_reason": r["last_reason"]}
    finally:
        if owns:
            db.close()
