One-off diagnostic scripts kept for reference, not part of the test suite and
not wired into the app. Several were written for the dockerised Linux run and
still carry hard-coded container paths (`/app/data/...`, `/tmp/...`), so they
will not run as-is on the Windows setup — read them before running one.

- `proxy_cf_wait.py` — does the Cloudflare interstitial clear within 40s on the
  apply tab? Used while investigating the apply-page block.
