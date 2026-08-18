r"""Copy a hand-made Upwork login from your PERSONAL browser into the agent.

Why: Upwork sometimes blocks a sign-in made in the agent's own separate profile
("Due to technical difficulties"), while the personal browser you use every day
signs in fine. Rather than hand the whole personal profile to the agent (which
would force you to keep that browser closed while it runs), this copies only the
SESSION: Upwork cookies are written to data/upwork_storage_state.json, which
open_context() injects into the agent's own profile via _bootstrap_cookies().

How it gets the cookies: it asks the running browser for them over DevTools
(CDP). Copying the profile's cookie database does NOT work -- modern Edge/Chrome
bind cookie encryption to the browser itself (App-Bound Encryption), so a copied
file decrypts to nothing. Letting the browser hand them over is the supported way,
and it means you do NOT have to close the browser.

Setup (once per export):
  1. Close Edge completely (stop_bot.bat clears its background processes).
  2. Start Edge with the debug port -- your normal profile, normal browsing:
       "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222
  3. Log in to Upwork in that window (if not already).
  4. python export_session.py

Privacy: only Upwork cookies are kept. Cookies for your other sites are filtered
out and never written to disk. The output file is gitignored.

Usage:
  python export_session.py              export from the browser on port 9222
  python export_session.py --port 9333  use a different debug port
  python export_session.py --check      report what is already saved
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import upwork_connect as uc

STATE_PATH = uc.BASE_DIR / "data" / "upwork_storage_state.json"
# Cookies that only exist once Upwork has actually authenticated you.
LOGIN_MARKERS = ("master_access_token", "oauth2_global_js_token", "user_uid")
EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def _upwork_cookies(cookies: list[dict]) -> list[dict]:
    """Upwork cookies only — everything else in the personal browser (mail,
    banking, ...) must never reach disk. Matched on the domain SUFFIX, so a
    look-alike like "upwork.com.example.org" is not treated as Upwork."""
    kept = []
    for c in cookies:
        dom = (c.get("domain") or "").lstrip(".").lower()
        if dom == "upwork.com" or dom.endswith(".upwork.com"):
            kept.append(c)
    return kept


def _report(cookies: list[dict]) -> bool:
    names = {c.get("name") for c in cookies}
    found = [m for m in LOGIN_MARKERS if m in names]
    print(f"  Upwork cookies: {len(cookies)}")
    print(f"  login markers:  {', '.join(found) if found else 'NONE'}")
    if not found:
        print("  -> This session does NOT look logged in.")
        return False
    print("  -> Looks like a valid logged-in session.")
    return True


def _how_to_start_browser(port: int) -> None:
    exe = EDGE_EXE if Path(EDGE_EXE).exists() else "msedge.exe"
    print(f"\nCould not reach a browser on port {port}.")
    print("Close Edge completely (run stop_bot.bat), then start it like this:\n")
    print(f'  "{exe}" --remote-debugging-port={port}\n')
    print("Log in to Upwork in that window, then re-run this script.")
    print("You can keep using that browser normally -- it stays open.\n")
    print("To check whether Edge is really listening:")
    print(f"  curl http://127.0.0.1:{port}/json/version")
    print(f"  netstat -ano | findstr :{port}")
    print("Nothing there means Edge ignored the flag -- it does that when another")
    print("Edge process is still alive, so run stop_bot.bat before starting it.")


def _arg_port(args: list[str]) -> int:
    if "--port" in args:
        try:
            return int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            pass
    return 9222


def main() -> int:
    args = sys.argv[1:]

    if "--check" in args:
        if not STATE_PATH.exists():
            print(f"No session file yet: {STATE_PATH}")
            return 1
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        print(f"Session file: {STATE_PATH}")
        return 0 if _report(_upwork_cookies(data.get("cookies") or [])) else 1

    port = _arg_port(args)
    from playwright.sync_api import sync_playwright

    print(f"Connecting to your browser on port {port}...")
    with sync_playwright() as p:
        browser = None
        # 127.0.0.1 FIRST, not "localhost": where localhost resolves to IPv6 the
        # connect fails with "ECONNREFUSED ::1" even though Edge is listening --
        # it binds the debug port on IPv4 only.
        for host in ("127.0.0.1", "localhost"):
            try:
                browser = p.chromium.connect_over_cdp(f"http://{host}:{port}", timeout=15000)
                break
            except Exception as e:  # noqa: BLE001 — expected when the port isn't open
                print(f"  {host}: {type(e).__name__}: {str(e)[:100]}")
        if browser is None:
            _how_to_start_browser(port)
            return 1
        try:
            cookies: list[dict] = []
            for ctx in browser.contexts:
                cookies.extend(ctx.cookies())
        finally:
            browser.close()  # detaches CDP; does NOT close your browser

    up = _upwork_cookies(cookies)
    print(f"\nBrowser had {len(cookies)} cookies; keeping {len(up)} Upwork ones "
          f"(dropping {len(cookies) - len(up)} unrelated to Upwork).")
    if not _report(up):
        print("\nNothing written. Log in to Upwork in that browser, then retry.")
        return 1

    for c in up:  # Playwright only accepts these three sameSite values
        if c.get("sameSite") not in ("Strict", "Lax", "None"):
            c.pop("sameSite", None)

    if STATE_PATH.exists():
        shutil.copy2(STATE_PATH, STATE_PATH.with_suffix(".json.bak"))
        print(f"  previous session backed up -> {STATE_PATH.name}.bak")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"cookies": up, "origins": []}, indent=1),
                          encoding="utf-8")
    print(f"\nSaved -> {STATE_PATH}")
    print("The agent injects these into its own profile on the next run.")
    print("Verify with:  python upwork_connect.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
