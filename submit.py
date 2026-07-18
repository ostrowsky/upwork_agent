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

def _select_applicant(page) -> bool:
    """On an agency account the apply form asks 'submit as freelancer or agency member?'
    and hides Send until chosen. Pick the identity (APPLY_AS=freelancer default)."""
    try:
        grp = page.locator(S.APPLY_AGENCY_SELECTOR).first
        if not grp.count():
            return False
        as_agency = os.getenv("APPLY_AS", "freelancer").strip().lower() == "agency"
        opt = page.locator(S.APPLY_AGENCY_RADIO if as_agency else S.APPLY_FREELANCER_RADIO).first
        if opt.count():
            opt.click()
            page.wait_for_timeout(1000)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _form_present(page) -> bool:
    """Definitive success signal: the apply form's cover-letter field rendered."""
    try:
        return bool(page.locator(S.APPLY_COVER_LETTER).first.count())
    except Exception:  # noqa: BLE001
        return False


def _wait_past_cloudflare(page, rounds: int = 24, step_ms: int = 1500) -> bool:
    """Wait out a Cloudflare 'Just a moment / Один момент' interstitial.

    Success = the apply form rendered (definitive, even if the title still lags)
    OR the page is no longer a challenge. A stuck managed challenge is nudged with
    one reload at the halfway point. A visible Edge on the real profile usually
    auto-clears these within ~30s. Returns False only if still blocked on timeout.
    """
    markers = S.CLOUDFLARE_TITLE_MARKERS
    reloaded = False
    for i in range(rounds):
        if _form_present(page):
            return True
        if not any(m in (page.title() or "").lower() for m in markers):
            return True
        if i == rounds // 2 and not reloaded:  # one reload often clears a stuck challenge
            reloaded = True
            try:
                page.reload(wait_until="domcontentloaded")
            except Exception:  # noqa: BLE001
                pass
        page.wait_for_timeout(step_ms)
    return _form_present(page) or not any(m in (page.title() or "").lower() for m in markers)


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
        # Last resort: match by accessible label text (survives markup changes
        # to the .air3-dropdown/aria-label structure the two selectors above
        # depend on) — seen live still showing the placeholder after both failed.
        toggle = page.get_by_label(re.compile(aria_contains, re.I)).first
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


def _textarea_label_text(page, textarea) -> str:
    """Best-effort label for a textarea: <label for=>, aria-label, the element
    aria-labelledby points to (Upwork's cover-letter pattern), or — for the
    per-job screening questions, where Upwork renders a <label id=""> sibling
    with NO aria-labelledby link at all — the nearest .form-group ancestor's
    <label>, matched purely by DOM position rather than accessibility wiring."""
    try:
        return (textarea.evaluate(
            "el => (el.labels && el.labels[0] && el.labels[0].innerText) || "
            "el.getAttribute('aria-label') || "
            "(el.getAttribute('aria-labelledby') && "
            " (document.getElementById(el.getAttribute('aria-labelledby').split(' ')[0]) || {}).innerText) || "
            "(() => { "
            "  const group = el.closest('.form-group') || el.parentElement; "
            "  const lbl = group && group.querySelector('label'); "
            "  return (lbl && lbl.innerText) || ''; "
            "})() || ''"
        ) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def generate_screening_answer(question: str, job: Job, proposal: Proposal) -> str:
    """LLM-written answer to a job-specific screening question (e.g. "Describe
    your recent experience with similar projects"), using the job + our own
    proposal/cases as context so the answer stays consistent with the pitch."""
    from ai import call_llm

    prompt = (
        f"You are answering a screening question on an Upwork job application.\n\n"
        f"Job title: {job.title}\n"
        f"Job description: {(job.description or '')[:1500]}\n\n"
        f"Our proposal cover letter (for context/consistency): {(proposal.content or '')[:1000]}\n\n"
        f"Screening question: {question}\n\n"
        "Write a short, specific, first-person answer (2-5 sentences). "
        "No preamble, no markdown — just the answer text."
    )
    try:
        return (call_llm([{"role": "user", "content": prompt}]) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _fill_screening_questions(page, job: Job, proposal: Proposal) -> int:
    """Fill any custom screening-question textareas beyond the cover letter.

    Some jobs add extra required free-text fields (e.g. "Please attach any
    portfolio with retro pixel art style.") in the same "Additional details"
    block as the cover letter. Left empty, Upwork blocks the send client-side
    with a per-field "Value is required" hint — no banner our error-detection
    can see, so it just looks like "not confirmed" with no reason.
    """
    filled = 0
    try:
        textareas = page.locator("textarea")
        n = textareas.count()
    except Exception:  # noqa: BLE001
        return 0
    for i in range(n):
        ta = textareas.nth(i)
        try:
            if not ta.is_visible():
                continue
            if (ta.input_value() or "").strip():
                continue  # already has content (cover letter, or pre-filled)
            label = _textarea_label_text(page, ta)
            if not label or "cover letter" in label.lower():
                continue
            answer = generate_screening_answer(label, job, proposal)
            if answer:
                ta.fill(answer)
                filled += 1
        except Exception:  # noqa: BLE001
            continue
    return filled


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
        r"more Connects needed|insufficient connects|insufficient\s+\w*\s*connects|"
        r"not enough Connects|Buy Connects|Get more Connects|MoreConnectsNeeded|need more connects",
        text, re.IGNORECASE))


def _is_boost_required(text: str | None) -> bool:
    """True if the apply page is a boost flow (job needs boosting connects to send)."""
    if not text:
        return False
    t = text.lower()
    return ("boost" in t) and ("send for" not in t)


def _read_connects_required(page) -> int | None:
    """Cost of THIS proposal — read from the 'Send for N Connects' button, or
    (on the plain "Submit proposal" button variant, which carries no amount)
    from the "This proposal requires N Connects" text earlier on the page.

    The page also shows the account balance ("72 Connects available"); we must
    not confuse it with the per-proposal cost, so we anchor on these two
    specific phrasings rather than any number near the word "Connects".
    """
    try:
        body = page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        return None
    m = re.search(S.CONNECTS_REGEX, body, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(S.CONNECTS_REQUIRED_REGEX, body, re.IGNORECASE)
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


_CLOSED_MARKERS = (
    "no longer available", "job is closed", "this job is closed",
    "error 403", "is no longer accepting", "job posting was removed",
)


def _body_text(page) -> str:
    """Robust full-page text. The apply page is heavy (~1.4MB) and inner_text('body')
    often times out → use document.body.innerText via evaluate, with a fallback."""
    try:
        t = page.evaluate("() => document.body ? document.body.innerText : ''")
        if t:
            return t
    except Exception:  # noqa: BLE001
        pass
    try:
        return page.inner_text("body", timeout=2500) or ""
    except Exception:  # noqa: BLE001
        return ""


def _is_job_closed(page) -> bool:
    """True if the apply page indicates the job has closed/been removed."""
    body = _body_text(page)
    if not body:
        return False
    t = body.lower()
    # Guard: only when the apply form is clearly absent (avoid false positives).
    if "cover letter" in t or "send for" in t:
        return False
    return any(m in t for m in _CLOSED_MARKERS)


def submit_log(line: str) -> None:
    """Append a one-line submit event to data/submit.log (so failed sends are findable)."""
    try:
        from pathlib import Path

        p = Path(__file__).resolve().parent / "data" / "submit.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {line}\n")
    except Exception:  # noqa: BLE001 — logging must never break a submit
        pass


FEED_URL = "https://www.upwork.com/nx/find-work/best-matches"


def apply_via_spa_enabled() -> bool:
    """In-SPA navigation to the apply form (default on). Set APPLY_VIA_SPA=0 to
    force the old deep-link goto."""
    return os.getenv("APPLY_VIA_SPA", "1").strip().lower() in ("1", "true", "yes")


def _spa_job_route(job: Job) -> str | None:
    """Client-side route (relative to the /nx/find-work/ router base) for a job's
    detail page. None if the job has no upwork id."""
    jid = (getattr(job, "upwork_job_id", "") or "").strip().lstrip("~")
    return f"/best-matches/details/~{jid}" if jid else None


def _dismiss_profile_modal(page) -> None:
    """Remove the fullscreen 'Complete your profile' modal that can cover the
    'Apply now' button (account not 100% complete). Best-effort."""
    try:
        page.evaluate("""() => {
          document.querySelectorAll('.complete-profile-modal, .is-modal-fullscreen, [data-test*="complete-your-profile"]').forEach(e => e.remove());
          document.body.classList.remove('air3-is-fullscreen-open');
          document.documentElement.style.overflow = 'auto';
        }""")
    except Exception:  # noqa: BLE001
        pass


def _navigate_apply_spa(page, job: Job) -> bool:
    """Reach the apply form by navigating INSIDE the SPA, not deep-linking.

    Upwork's Cloudflare serves an unbeatable "Just a moment" challenge to
    datacenter IPs on a *full-page load* of /jobs/ or /apply/. But the feed
    (/nx/find-work/best-matches) passes, and client-side navigation from there
    (Vue router.push → job detail → "Apply now") loads the form via XHR reusing
    the feed's cf_clearance — no fresh challenge. This is how a human reaches it.

    Returns True if the apply form opened. Requires a real Playwright page
    (get_by_role); returns False on a fake/test page so callers fall back.
    """
    if not hasattr(page, "get_by_role"):
        return False
    route = _spa_job_route(job)
    if not route:
        return False
    page.goto(FEED_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    if not _wait_past_cloudflare(page):  # the feed itself should clear CF
        return False
    # Client-side route change to the job detail (no full load → no challenge).
    page.evaluate("(p) => { try { window.$nuxt.$router.push(p); } catch (e) {} }", route)
    # Wait for the detail to render, dismiss the profile modal, click "Apply now".
    clicked = False
    for _ in range(12):
        page.wait_for_timeout(1500)
        _dismiss_profile_modal(page)
        try:
            btn = page.get_by_role("button", name="Apply now").first
            if btn.count():
                btn.click(timeout=4000)
                clicked = True
                break
        except Exception:  # noqa: BLE001
            continue
    if not clicked:
        return False
    # Wait for the apply form (cover letter) or the /apply/ URL.
    for _ in range(12):
        page.wait_for_timeout(1500)
        if _form_present(page) or "/apply" in (getattr(page, "url", "") or ""):
            return True
    return False


def _open_apply_page(page, url: str, job: Job) -> bool:
    """Get the page onto the apply form. Tries Cloudflare-safe SPA navigation
    first, then falls back to the deep-link goto. Returns False if still blocked."""
    if apply_via_spa_enabled():
        try:
            if _navigate_apply_spa(page, job):
                return True
            submit_log(f"job#{getattr(job, 'id', '?')} spa-nav failed → deep-link fallback")
        except Exception as e:  # noqa: BLE001
            submit_log(f"job#{getattr(job, 'id', '?')} spa-nav error: {type(e).__name__}: {str(e)[:80]}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    return _wait_past_cloudflare(page)


def _apply_on_page(page, url: str, job: Job, proposal: Proposal, db, dry_run: bool) -> dict:
    """Drive the apply form on an already-open page. Browser-agnostic, so it is
    unit-testable with a fake page object (see tests/test_submit_form.py)."""
    from upwork_connect import is_logged_in

    # In-SPA navigation avoids Cloudflare on datacenter IPs; deep-link goto fallback.
    if not _open_apply_page(page, url, job):
        _dump_apply_debug(page)
        return {"ok": False, "submitted": False, "dry_run": dry_run,
                "reason": "blocked by Cloudflare challenge (debug dumped)", "connects": None}

    authed, why = is_logged_in(page)
    if not authed and "apply" not in page.url:
        return {"ok": False, "submitted": False, "dry_run": dry_run,
                "reason": f"not authenticated: {why}", "connects": None}

    # Job may have closed since ingestion → Upwork serves "no longer available" / 403.
    if _is_job_closed(page):
        return {"ok": False, "submitted": False, "dry_run": dry_run, "closed": True,
                "reason": "job closed (no longer available on Upwork)", "connects": None}

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

    # The apply form has THREE textareas (cover letter, portfolio note, similar-
    # experience note). CSS fallbacks as broad as `textarea` pick whichever one
    # is first in DOM order — if Upwork reshuffles the form, that silently fills
    # the wrong box and leaves the real cover letter empty (Upwork then rejects
    # the submit client-side, which looks like "not confirmed" with no error we
    # can detect). Match by the visible "Cover Letter" label first — that's tied
    # to the field semantically, not by position — and only fall back to the
    # old positional selector if no labelled field is found.
    cover = page.get_by_label(re.compile("cover letter", re.I)).first
    if not cover.count():
        cover = page.locator(cover_sel).first
    filled = False
    if cover.count():
        cover.fill(proposal.content)
        filled = True

    screening_filled = _fill_screening_questions(page, job, proposal)

    # Agency accounts: pick the applicant identity (freelancer/agency), else Send is hidden.
    _select_applicant(page)

    # Attach the generated case study PDF(s) for this job, if any (best-effort).
    attach = {"attached": 0, "reason": "disabled"}
    if attach_case_enabled():
        attach = _attach_files(page, attachment_paths_for_job(db, getattr(job, "id", None)))

    bid = _fill_bid(page, job, proposal)
    rate_increase = _fill_rate_increase(page)
    connects = _read_connects_required(page)
    # Cache the per-proposal cost so the UI can show "Отправить за N connects".
    # No commit here (the SENT path commits; dry-run commits in its branch below).
    if connects is not None and proposal is not None:
        try:
            proposal.connects_cost = connects
        except Exception:  # noqa: BLE001
            pass
    # Capture the page text for connects parsing. NOTE: we do NOT persist the
    # apply-form "N available" here — it proved unreliable (showed a stale/wrong
    # value that clobbered the authoritative Connects-page balance). Balance is
    # persisted only from (a) connects.fetch_balance_live() and (b) the confirmed
    # post-submit "N remaining" below.
    balance_text = _body_text(page)

    if dry_run:
        dbg = _dump_apply_debug(page)
        if connects is not None and proposal is not None:
            try:  # persist the cost read on this dry-run for the UI button label
                db.commit()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "submitted": False, "dry_run": True,
                "reason": (f"dry-run (filled={filled}, screening={screening_filled}, bid={bid}, "
                           f"rate_inc={rate_increase}, "
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
        if _is_boost_required(balance_text):
            return {"ok": False, "submitted": False, "dry_run": False,
                    "reason": "boost required — this job needs a boosted proposal "
                              "(not enough connects to boost); skip or top up",
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
                        submit_log(f"job#{job.id} dry={dry_run} submitted={res.get('submitted')} "
                                   f"connects={res.get('connects')} attached={res.get('attached')} "
                                   f"reason={(res.get('reason') or '')[:120]}")
                        # A job that closed since ingestion → mark CLOSED so it leaves the list.
                        if res.get("closed") and getattr(job, "status", None) != "SENT":
                            try:
                                job.status = "CLOSED"
                                db.commit()
                            except Exception:  # noqa: BLE001
                                pass
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
