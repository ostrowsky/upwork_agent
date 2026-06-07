"""Centralized Upwork selectors — the single source of truth for Upwork's DOM.

Upwork changes its markup periodically to deter scraping. When ingestion or the
apply flow breaks, update selectors HERE ONLY — the rest of the code imports
from this module.

Convention: each selector is an OR-list ordered most-stable → least-stable:
  1. ARIA / role / aria-label / aria-labelledby   (tied to accessibility)
  2. data-test / data-cy / data-ev-*              (their own test hooks)
  3. visible text (:has-text)                     (survives restyling)
  4. hashed CSS classes (air3-*, data-v-*)        (LAST resort — most volatile)

`verified:` notes record the last time the selector was confirmed against the
live site, so drift is auditable.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
BEST_MATCHES_URL = "https://www.upwork.com/nx/find-work/best-matches"
APPLY_URL_TEMPLATE = "https://www.upwork.com/nx/proposals/job/{job_id}/apply/"
AUTH_CHECK_URLS = (
    "https://www.upwork.com/nx/find-work/",
    "https://www.upwork.com/nx/proposals/",
)

# ---------------------------------------------------------------------------
# Cloudflare interstitial (title markers)
# ---------------------------------------------------------------------------
CLOUDFLARE_TITLE_MARKERS = ("момент", "moment", "challenge", "just a moment")

# ---------------------------------------------------------------------------
# Feed / ingestion — verified 2026-06 on find-work/best-matches
# ---------------------------------------------------------------------------
FEED_TILE_SELECTORS = (
    "section.air3-card-section.air3-card-hover",
    "section:has(h3.job-tile-title)",
    '[data-test="job-tile"]',
    'section[data-test="JobTile"]',
    'article[data-test="JobTile"]',
)
FEED_TITLE_LINK = (
    'h3.job-tile-title a, a[data-test="job-tile-title-link"], a[href*="/jobs/"]'
)
FEED_DESCRIPTION = (
    '[data-test="job-description-text"], [data-test="job-description-line-clamp"]'
)
# job-type carries a clean budget string ("Fixed-price", "Hourly: $15-$30").
FEED_BUDGET = '[data-test="job-type"]'

# Preferred, more stable source: the feed is SSR'd into window.__NUXT__ (no XHR
# API). Evaluating it returns structured jobs — robust to CSS/markup changes.
# Path verified 2026-06: window.__NUXT__.state.feedBestMatch.jobs (array of job objects).
NUXT_FEED_JOBS_JS = (
    "() => { const s = window.__NUXT__ && window.__NUXT__.state; "
    "return (s && s.feedBestMatch && s.feedBestMatch.jobs) || null; }"
)

# ---------------------------------------------------------------------------
# Apply form ("Submit a proposal") — verified 2026-06
# ---------------------------------------------------------------------------
APPLY_COVER_LETTER = (
    'textarea[aria-labelledby="cover_letter_label"], '
    'textarea[aria-labelledby*="cover"], textarea[name="coverLetter"], '
    "textarea.air3-textarea, textarea"
)
# Hourly bid ("Your bid"). #fee-rate (disabled) and #receive-step-rate are different.
APPLY_RATE_HOURLY = '#step-rate, input[data-test="currency-input"]:not([disabled])'
APPLY_RATE_FIXED = 'input[data-test="currency-input"]:not([disabled])'

# "Schedule a rate increase" dropdowns: aria-label is on the parent .air3-dropdown.
def apply_rate_increase_toggle(aria_contains: str) -> str:
    return f'.air3-dropdown[aria-label*="{aria_contains}"] [data-test="dropdown-toggle"]'


def apply_rate_increase_toggle_fallback(aria_contains: str) -> str:
    return f'[role="combobox"][aria-label*="{aria_contains}"]'


APPLY_DROPDOWN_OPTION = (
    '.air3-menu-item:visible, .air3-dropdown-menu li:visible, [role="option"]:visible'
)
APPLY_PERCENT_TOGGLE = '.air3-dropdown[aria-label*="How much"] [data-test="dropdown-toggle"]'

# Primary submit button reads "Send for N Connects".
APPLY_SEND_BUTTON = (
    'button.air3-btn-primary:has-text("Send for"), '
    'button:has-text("Send for"), '
    'button[data-test="submit-proposal-button"], '
    'button.air3-btn-primary:has-text("Send")'
)
APPLY_ERROR = (
    '[role="alert"], .air3-alert-danger, [data-test*="error"], .up-alert-content'
)
# Some flows pop a final confirmation dialog after the first Send click.
# Best-effort: a primary Send/Submit button INSIDE a modal dialog.
APPLY_CONFIRM_BUTTON = (
    '[role="dialog"] button:has-text("Send for"), '
    '[role="dialog"] button:has-text("Send"), '
    '[role="dialog"] button:has-text("Submit"), '
    '.air3-modal button.air3-btn-primary'
)
# Attachment file input on the apply form (hidden <input type=file>).
# Candidates tried in order; we set files directly on the input (no click needed).
APPLY_ATTACH_INPUT = (
    'input[type="file"][data-test="file-uploader-input"], '
    'input[type="file"][name*="attachment"], '
    '[data-test*="attachment"] input[type="file"], '
    '[data-test*="upload"] input[type="file"], '
    'input[type="file"][accept*="pdf"], '
    'input[type="file"]'
)

# Per-proposal connects cost — anchor on the Send button, NOT the account balance.
CONNECTS_REGEX = r"Send for\s+(\d+)\s+Connects"
# Account connects balance, as shown on the apply page (gap #4).
CONNECTS_BALANCE_REGEX = (
    r"(\d+)\s+Connects available"
    r"|have\s+(\d+)\s+Connects remaining"
    r"|Remaining balance:\s*(\d+)\s+Connects"
)

# ---------------------------------------------------------------------------
# Messenger compose box (best-effort; tune via msg_compose_debug dump).
# ---------------------------------------------------------------------------
MSG_COMPOSE_INPUT = (
    '[data-test="message-form-editor"] [contenteditable="true"], '
    'div[role="textbox"][contenteditable="true"], '
    '[data-test*="message"] [contenteditable="true"], '
    'textarea[placeholder*="message" i], '
    'div[contenteditable="true"]'
)
MSG_SEND_BUTTON = (
    '[data-test="send-message-button"], button[data-test*="send"], '
    'button[aria-label*="Send" i], button:has-text("Send")'
)
