"""Day 7 — submit form-driving logic, tested with a fake page (no real browser).

Covers the money-touching part of submit.py that the guard tests don't:
- the apply form is filled (cover letter, rate cleared+typed, rate-increase).
- dry-run never sends; reason reports filled/bid/rate_inc.
- LIVE success → proposal/job marked SENT only after redirect confirmation.
- LIVE error banner or no redirect → NOT marked SENT (no false positive).
- missing Send button → not sent.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import submit  # noqa: E402
import upwork_selectors as S  # noqa: E402


class FakeLocator:
    def __init__(self, count=1, text="", on_click=None):
        self._count = count
        self._text = text
        self.on_click = on_click
        self.actions = []

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_visible(self, **k):
        return self._count > 0

    def click(self, **k):
        self.actions.append("click")
        if self.on_click:
            self.on_click()

    def fill(self, value, **k):
        self.actions.append(("fill", value))

    def type(self, value, delay=None):
        self.actions.append(("type", value))

    def press(self, key, **k):
        self.actions.append(("press", key))

    def scroll_into_view_if_needed(self, **k):
        pass

    def inner_text(self, **k):
        return self._text

    def nth(self, i):
        return self


class FakePage:
    def __init__(self, locators, body="", title="submit a proposal", url=""):
        self._locators = locators
        self._body = body
        self._title = title
        self.url = url
        self.mouse = types.SimpleNamespace(wheel=lambda *a, **k: None)

    def goto(self, url, **k):
        self.url = url

    def reload(self, **k):
        pass

    def wait_for_timeout(self, ms):
        pass

    def wait_for_selector(self, sel, timeout=None):
        pass  # no-op; the real waits just gate timing

    def locator(self, sel):
        return self._locators.get(sel, FakeLocator(count=0))

    def get_by_label(self, label, **k):
        # No fixture sets up a labelled match — every test exercises the
        # positional-selector fallback, same as before this method existed.
        return FakeLocator(count=0)

    def inner_text(self, sel, timeout=None):
        return self._body if sel == "body" else ""

    def evaluate(self, expr):
        return self._body  # stands in for document.body.innerText

    def title(self):
        return self._title

    def content(self):
        return "<html></html>"

    def screenshot(self, **k):
        pass


class FakeTextareaEl:
    """One distinct textarea element, for tests that need several at once
    (page.locator("textarea") normally returns ONE FakeLocator shared across
    matches — not enough to simulate several screening-question boxes)."""

    def __init__(self, label="", value="", visible=True):
        self._label = label
        self._value = value
        self._visible = visible
        self.filled_with = None

    def is_visible(self, **k):
        return self._visible

    def input_value(self, **k):
        return self._value

    def evaluate(self, expr, **k):
        return self._label

    def fill(self, value, **k):
        self.filled_with = value
        self._value = value


class FakeMultiLocator:
    """`page.locator("textarea")` matching several distinct elements."""

    def __init__(self, items):
        self._items = items

    def count(self):
        return len(self._items)

    def nth(self, i):
        return self._items[i]


class FakeDB:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


APPLY_URL = "https://www.upwork.com/nx/proposals/job/~021aaaaaaaaaaaaaaa/apply/"


@pytest.fixture(autouse=True)
def _no_disk_dumps(monkeypatch):
    # Don't write debug files during tests.
    monkeypatch.setattr(submit, "_dump_apply_debug", lambda page: "stub")
    monkeypatch.setattr(submit, "_dump_post_submit_debug", lambda page: "stub")
    monkeypatch.setenv("BID_HOURLY_RATE", "35")


def _job():
    return types.SimpleNamespace(id=1, title="Unity job", budget="Hourly", status="PROPOSAL_DRAFTED")


def _proposal():
    return types.SimpleNamespace(id=1, job_id=1, status="DRAFT", content="cover text",
                                 submitted_at=None, connects_spent=None)


def _base_locators(send_on_click=None, error_count=0):
    return {
        S.APPLY_COVER_LETTER: FakeLocator(count=1),
        S.APPLY_RATE_HOURLY: FakeLocator(count=1),
        S.apply_rate_increase_toggle("How often"): FakeLocator(count=1),
        S.APPLY_DROPDOWN_OPTION: FakeLocator(count=1),
        S.APPLY_PERCENT_TOGGLE: FakeLocator(count=0),  # "Never" hides percent
        S.APPLY_SEND_BUTTON: FakeLocator(count=1, on_click=send_on_click),
        S.APPLY_ERROR: FakeLocator(count=error_count, text="Please fix the errors below"),
    }


def test_fixed_price_fills_bid_from_estimate(monkeypatch):
    """Gap #1: a fixed-price job fills the FIXED bid field from the estimate."""
    monkeypatch.delenv("BID_FIXED_AMOUNT", raising=False)
    job = types.SimpleNamespace(id=2, title="Fixed job", budget="Fixed-price", status="PROPOSAL_DRAFTED")
    proposal = types.SimpleNamespace(id=2, job_id=2, status="DRAFT",
                                     content="cover", estimate="Phases… Total: $20,000",
                                     submitted_at=None, connects_spent=None)
    locs = _base_locators()
    locs[S.APPLY_RATE_FIXED] = FakeLocator(count=1)  # fixed-price bid field present
    page = FakePage(locs, body="Send for 16 Connects", url=APPLY_URL)

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, FakeDB(), dry_run=True)
    assert res["dry_run"] is True
    assert "bid=20000" in res["reason"]  # derived from estimate, not hardcoded
    assert ("type", "20000") in locs[S.APPLY_RATE_FIXED].actions


def test_dry_run_fills_form_without_sending():
    locs = _base_locators()
    page = FakePage(locs, body="Send for 16 Connects · 72 Connects available", url=APPLY_URL)
    job, proposal, db = _job(), _proposal(), FakeDB()

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=True)

    assert res["dry_run"] is True and res["submitted"] is False
    assert res["connects"] == 16
    assert proposal.connects_cost == 16  # cached for the «Отправить за N connects» button
    assert "filled=True" in res["reason"] and "bid=35" in res["reason"] and "rate_inc=True" in res["reason"]
    # cover letter filled, rate cleared (Ctrl+a/Delete) then typed
    assert ("fill", "cover text") in locs[S.APPLY_COVER_LETTER].actions
    assert ("type", "35") in locs[S.APPLY_RATE_HOURLY].actions
    assert "click" in locs[S.APPLY_DROPDOWN_OPTION].actions  # dropdown option chosen
    assert proposal.status == "DRAFT"  # never touched on dry-run


def test_live_success_marks_sent_after_redirect():
    job, proposal, db = _job(), _proposal(), FakeDB()
    page_ref = {}

    def redirect_away():
        page_ref["page"].url = "https://www.upwork.com/nx/proposals/"  # left /apply/

    locs = _base_locators(send_on_click=redirect_away, error_count=0)
    page = FakePage(locs, body="Send for 16 Connects", url=APPLY_URL)
    page_ref["page"] = page

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=False)

    assert res["submitted"] is True and res["ok"] is True
    assert "click" in locs[S.APPLY_SEND_BUTTON].actions
    assert proposal.status == "SENT"
    assert proposal.connects_spent == 16
    assert proposal.submitted_at is not None
    assert job.status == "SENT"
    assert db.commits == 1


def test_live_success_by_title_even_if_url_ambiguous():
    """Regression: page redirects to 'My proposals' (title 'Proposals') after Send.
    A benign role=alert on that page must NOT cause a false 'not confirmed'
    (which spent connects without recording SENT)."""
    job, proposal, db = _job(), _proposal(), FakeDB()
    page_ref = {}

    def go_to_proposals():
        page_ref["page"]._title = "Proposals"  # redirected to the list

    locs = _base_locators(send_on_click=go_to_proposals, error_count=1)  # benign alert present
    page = FakePage(locs, body="Send for 16 Connects", url=APPLY_URL)  # url still has /apply
    page_ref["page"] = page

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=False)
    assert res["submitted"] is True  # title=Proposals → success despite the alert
    assert proposal.status == "SENT"


def test_live_confirmation_modal_is_clicked():
    """A final confirm dialog after Send is clicked, then success is verified."""
    job, proposal, db = _job(), _proposal(), FakeDB()
    page_ref = {}

    def confirm_redirect():
        page_ref["page"].url = "https://www.upwork.com/nx/proposals/"

    locs = _base_locators(send_on_click=None, error_count=0)
    # confirm modal present; clicking it completes the submit (redirect)
    locs[S.APPLY_CONFIRM_BUTTON] = FakeLocator(count=1, on_click=confirm_redirect)
    page = FakePage(locs, body="Send for 16 Connects", url=APPLY_URL)
    page_ref["page"] = page

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=False)

    assert "click" in locs[S.APPLY_CONFIRM_BUTTON].actions
    assert res["submitted"] is True
    assert proposal.status == "SENT"


def test_live_error_banner_does_not_send():
    """Send clicked but an error banner remains → must NOT mark SENT."""
    job, proposal, db = _job(), _proposal(), FakeDB()
    # No redirect (stays on /apply/) and an error banner is present.
    locs = _base_locators(send_on_click=None, error_count=1)
    page = FakePage(locs, body="Send for 16 Connects", url=APPLY_URL)

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=False)

    assert res["submitted"] is False
    assert "not confirmed" in res["reason"]
    assert proposal.status == "DRAFT"  # unchanged — no false SENT
    assert job.status == "PROPOSAL_DRAFTED"
    assert db.commits == 0


def test_live_insufficient_connects_reported_clearly():
    """No Send button + 'More Connects needed' text → clear insufficient reason, no SENT."""
    job, proposal, db = _job(), _proposal(), FakeDB()
    locs = _base_locators()
    locs[S.APPLY_SEND_BUTTON] = FakeLocator(count=0)
    page = FakePage(locs, body="You need more Connects needed to submit", url=APPLY_URL)

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=False)
    assert res["submitted"] is False
    assert "insufficient connects" in res["reason"]
    assert proposal.status == "DRAFT"  # not submitted


def test_agency_selector_picks_freelancer():
    grp = FakeLocator(count=1)
    radio = _AttachLocator(count=1)  # reuse: has click() that records
    page = FakePage({S.APPLY_AGENCY_SELECTOR: grp, S.APPLY_FREELANCER_RADIO: radio})
    assert submit._select_applicant(page) is True
    assert "click" in radio.actions


def test_agency_selector_absent_is_noop():
    page = FakePage({})  # no agency selector → solo freelancer account
    assert submit._select_applicant(page) is False


def test_live_boost_required_reported_clearly():
    """No Send button + a boost flow (no 'Send for') → clear 'boost required', not a vague error."""
    job, proposal, db = _job(), _proposal(), FakeDB()
    locs = _base_locators()
    locs[S.APPLY_SEND_BUTTON] = FakeLocator(count=0)
    page = FakePage(locs, body="Boost your proposal to stand out. Available Connects: 5", url=APPLY_URL)

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=False)
    assert res["submitted"] is False
    assert "boost required" in res["reason"]
    assert proposal.status == "DRAFT"


def test_cloudflare_form_present_beats_challenge_title():
    """If the apply form rendered, treat as cleared even if the title still says 'just a moment'."""
    locs = {S.APPLY_COVER_LETTER: FakeLocator(count=1)}
    page = FakePage(locs, title="just a moment...")
    assert submit._wait_past_cloudflare(page, rounds=3) is True


def test_cloudflare_persistent_challenge_times_out():
    page = FakePage({}, title="just a moment...")  # no form, stays a challenge
    assert submit._wait_past_cloudflare(page, rounds=2, step_ms=0) is False


def test_live_closed_job_detected_and_marked():
    """A closed job ('no longer available') → reason 'job closed' + closed flag, not a vague error."""
    job, proposal, db = _job(), _proposal(), FakeDB()
    page = FakePage(_base_locators(), body="This job is no longer available. Error 403 (N)", url=APPLY_URL)

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=False)
    assert res["submitted"] is False
    assert res.get("closed") is True
    assert "job closed" in res["reason"]


def test_is_job_closed_guards_against_false_positive():
    # A normal apply form must NOT be flagged closed even if text is odd.
    page_ok = FakePage({}, body="Cover letter ... Send for 12 Connects")
    assert submit._is_job_closed(page_ok) is False
    page_closed = FakePage({}, body="This job is no longer available")
    assert submit._is_job_closed(page_closed) is True


def test_insufficient_detector_variants():
    f = submit._is_insufficient_connects
    assert f("insufficient Connects balance") is True
    assert f("You need more connects") is True
    assert f("MoreConnectsNeededModal shown") is True
    assert f("Send for 12 Connects") is False
    assert submit._is_boost_required("Boost your proposal") is True
    assert submit._is_boost_required("Send for 12 Connects — Boost optional") is False  # Send present


def test_live_missing_send_button():
    job, proposal, db = _job(), _proposal(), FakeDB()
    locs = _base_locators()
    locs[S.APPLY_SEND_BUTTON] = FakeLocator(count=0)
    page = FakePage(locs, body="Send for 16 Connects", url=APPLY_URL)

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=False)

    assert res["submitted"] is False
    assert "submit button not found" in res["reason"]
    assert proposal.status == "DRAFT"


class _AttachLocator(FakeLocator):
    def __init__(self, count=1):
        super().__init__(count=count)
        self.files = None

    def set_input_files(self, paths, **k):
        self.files = paths


def test_attach_files_uploads_to_input():
    inp = _AttachLocator(count=1)
    page = FakePage({S.APPLY_ATTACH_INPUT: inp})
    res = submit._attach_files(page, ["/tmp/case_1.pdf", "/tmp/case_2.pdf"])
    assert res["attached"] == 2
    assert inp.files == ["/tmp/case_1.pdf", "/tmp/case_2.pdf"]


def test_attach_files_verifies_filename_on_page():
    inp = _AttachLocator(count=1)
    # Form echoes the uploaded filename → verification should confirm it.
    page = FakePage({S.APPLY_ATTACH_INPUT: inp}, body="Attachments: case_1.pdf uploaded")
    res = submit._attach_files(page, ["/tmp/case_1.pdf"])
    assert res["attached"] == 1 and res["verified"] == 1 and res["reason"] == "ok"


def test_attach_files_unverified_when_name_absent():
    inp = _AttachLocator(count=1)
    page = FakePage({S.APPLY_ATTACH_INPUT: inp}, body="(no filename shown)")
    res = submit._attach_files(page, ["/tmp/case_1.pdf"])
    assert res["attached"] == 1 and res["verified"] == 0
    assert "not confirmed" in res["reason"]


def test_attach_files_no_input_is_noop():
    page = FakePage({})  # no file input on the form
    res = submit._attach_files(page, ["/tmp/case_1.pdf"])
    assert res["attached"] == 0
    assert "no file input" in res["reason"]


def test_attach_files_empty_paths():
    res = submit._attach_files(FakePage({}), [])
    assert res["attached"] == 0


def test_attachment_paths_defensive_on_bad_db():
    # FakeDB has no .query → must not raise, returns [].
    proposal = _proposal()
    proposal.selected_cases = "[1, 2]"
    assert submit.attachment_paths_for_proposal(FakeDB(), proposal) == []
    assert submit.attachment_paths_for_proposal(FakeDB(), None) == []


def test_attachment_paths_no_selected_cases():
    # _proposal() has no selected_cases attribute at all → must not raise.
    assert submit.attachment_paths_for_proposal(FakeDB(), _proposal()) == []


def test_empty_rate_config_keeps_profile_default(monkeypatch):
    """BID_HOURLY_RATE='' → don't touch the rate field (keep Upwork's default)."""
    monkeypatch.setenv("BID_HOURLY_RATE", "")
    locs = _base_locators()
    page = FakePage(locs, body="Send for 16 Connects", url=APPLY_URL)
    job, proposal, db = _job(), _proposal(), FakeDB()

    res = submit._apply_on_page(page, APPLY_URL, job, proposal, db, dry_run=True)

    assert "bid=None" in res["reason"]
    assert locs[S.APPLY_RATE_HOURLY].actions == []  # field untouched


def test_screening_questions_filled_and_cover_letter_skipped(monkeypatch):
    """Custom screening-question textareas beyond the cover letter get an
    LLM-generated answer; already-filled, unlabeled, hidden, or the cover
    letter box itself are left alone."""
    calls = []
    monkeypatch.setattr(
        submit, "generate_screening_answer",
        lambda q, job, proposal: (calls.append(q), f"ANSWER for: {q}")[1],
    )

    cover_el = FakeTextareaEl(label="Cover Letter", value="already has text")
    q1 = FakeTextareaEl(label="Describe your recent experience with similar projects", value="")
    q2 = FakeTextareaEl(label="Please attach any portfolio with retro pixel art style.", value="")
    already_has_value = FakeTextareaEl(label="Some other filled field", value="not empty")
    unlabeled = FakeTextareaEl(label="", value="")
    hidden = FakeTextareaEl(label="Hidden question", value="", visible=False)

    locs = _base_locators()
    locs["textarea"] = FakeMultiLocator([cover_el, q1, q2, already_has_value, unlabeled, hidden])
    page = FakePage(locs, body="Send for 16 Connects", url=APPLY_URL)
    job, proposal = _job(), _proposal()

    n = submit._fill_screening_questions(page, job, proposal)

    assert n == 2
    assert q1.filled_with == "ANSWER for: Describe your recent experience with similar projects"
    assert q2.filled_with == "ANSWER for: Please attach any portfolio with retro pixel art style."
    assert cover_el.filled_with is None
    assert already_has_value.filled_with is None
    assert unlabeled.filled_with is None
    assert hidden.filled_with is None
    assert calls == [
        "Describe your recent experience with similar projects",
        "Please attach any portfolio with retro pixel art style.",
    ]


def test_generate_screening_answer_uses_job_and_proposal_context(monkeypatch):
    captured = {}

    def fake_call_llm(messages, **k):
        captured["prompt"] = messages[0]["content"]
        return "  A concrete, first-person answer.  "

    monkeypatch.setattr("ai.call_llm", fake_call_llm)
    job = types.SimpleNamespace(id=1, title="Unity multiplayer job", description="Needs Photon rollback netcode.")
    proposal = types.SimpleNamespace(content="We built a rollback netcode system for...")

    answer = submit.generate_screening_answer(
        "Have you previously worked with Photon Quantum?", job, proposal
    )

    assert answer == "A concrete, first-person answer."
    assert "Photon Quantum" in captured["prompt"]
    assert "Unity multiplayer job" in captured["prompt"]
    assert "rollback netcode system" in captured["prompt"]


def test_generate_screening_answer_returns_empty_on_llm_error(monkeypatch):
    def boom(messages, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr("ai.call_llm", boom)
    job = types.SimpleNamespace(id=1, title="X", description="Y")
    proposal = types.SimpleNamespace(content="Z")

    assert submit.generate_screening_answer("Q?", job, proposal) == ""


def test_fill_screening_questions_defensive_when_locator_errors():
    page = FakePage({}, body="", url=APPLY_URL)  # "textarea" not in _locators → FakeLocator(count=0)
    job, proposal = _job(), _proposal()
    assert submit._fill_screening_questions(page, job, proposal) == 0
