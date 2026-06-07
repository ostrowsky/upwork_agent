"""
Подключение к Upwork через Playwright + сохранённая сессия Chrome.

Использование:
  python upwork_connect.py              # проверка сессии
  python upwork_connect.py --login      # войти вручную в профиль проекта (один раз)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
PROJECT_PROFILE = BASE_DIR / "data" / "upwork_profile"

# Страницы, доступные только после входа
AUTH_CHECK_URLS = [
    "https://www.upwork.com/nx/find-work/",
    "https://www.upwork.com/nx/proposals/",
]

LOGIN_HINTS = (
    "account-security/login",
    "/login",
    "Log in to Upwork",
)


def chrome_user_data() -> Path | None:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    path = Path(local) / "Google" / "Chrome" / "User Data"
    return path if path.is_dir() else None


def edge_user_data() -> Path | None:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    path = Path(local) / "Microsoft" / "Edge" / "User Data"
    return path if path.is_dir() else None


def is_real_user_data_dir(profile_dir: Path) -> bool:
    """True if the dir is a real browser 'User Data' root (Chrome/Edge),
    where the logged-in profile lives in a `--profile-directory` subfolder."""
    s = str(profile_dir)
    return "User Data" in s and ("Chrome" in s or "Edge" in s)


def project_profile_dir() -> Path:
    """Per-browser project profile so Chrome/Edge logins don't mix.

    chrome keeps the historical path (`data/upwork_profile`) to preserve the
    existing saved session; other channels get a suffixed dir.
    """
    channel = browser_channel()
    if channel == "chrome":
        return PROJECT_PROFILE
    return PROJECT_PROFILE.parent / f"upwork_profile_{channel}"


def pick_profile_dir() -> Path:
    custom = os.getenv("UPWORK_USER_DATA_DIR", "").strip()
    if custom:
        return Path(custom)
    if os.getenv("UPWORK_USE_CHROME_PROFILE", "1").strip() in ("1", "true", "yes"):
        chrome = chrome_user_data()
        if chrome:
            return chrome
    return project_profile_dir()


def is_logged_in(page) -> tuple[bool, str]:
    url = page.url.lower()
    title = (page.title() or "").lower()

    for hint in LOGIN_HINTS:
        if hint.lower() in url:
            return False, "redirected to login"

    if "log in to your upwork" in title:
        return False, "login page title"

    if any(x in title for x in ("challenge", "moment", "момент", "just a moment")):
        return False, "cloudflare challenge"

    if any(x in url for x in ("find-work", "best-matches", "/nx/proposals", "/ab/messages")):
        if "login" not in url:
            return True, "authenticated area"

    if page.locator('[data-test="job-tile"]').count() > 0:
        return True, "job feed visible"

    return False, f"unclear (url={page.url[:80]})"


def headless_mode() -> bool:
    return os.getenv("UPWORK_HEADLESS", "0").strip().lower() in ("1", "true", "yes")


def browser_channel() -> str:
    """Which Chromium-family browser to drive.

    chrome  — установленный Google Chrome (нужно закрыть свой Chrome)
    msedge  — Microsoft Edge (не конфликтует с повседневным Chrome) [рекомендуется]
    chromium — встроенный в Playwright Chromium (playwright install chromium)
    """
    return os.getenv("UPWORK_BROWSER_CHANNEL", "chrome").strip().lower() or "chrome"


def open_context(playwright, profile_dir: Path, headless: bool):
    profile_dir.mkdir(parents=True, exist_ok=True)
    kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "viewport": {"width": 1280, "height": 900},
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    channel = browser_channel()
    # "chromium" = bundled Playwright build → no channel; others are OS-installed channels.
    if channel != "chromium":
        kwargs["channel"] = channel
    # Real Chrome/Edge 'User Data' root → the logged-in profile is a subfolder.
    if is_real_user_data_dir(profile_dir):
        sub = os.getenv("UPWORK_PROFILE_DIRECTORY", "Default").strip() or "Default"
        kwargs["args"] = kwargs["args"] + [f"--profile-directory={sub}"]
    return playwright.chromium.launch_persistent_context(**kwargs)


def try_auto_login(page) -> None:
    email = os.getenv("UPWORK_EMAIL", "").strip()
    password = os.getenv("UPWORK_PASSWORD", "").strip()
    if not email or not password:
        return

    page.goto(
        "https://www.upwork.com/ab/account-security/login",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_timeout(2500)

    for sel in ['input#login_username', 'input[name="username"]', 'input[type="email"]']:
        if page.locator(sel).count():
            page.locator(sel).first.fill(email)
            break

    for sel in ['button#login_password_continue', 'button:has-text("Continue")']:
        if page.locator(sel).count():
            page.locator(sel).first.click()
            page.wait_for_timeout(2000)
            break

    for sel in ['input#login_password', 'input[name="password"]', 'input[type="password"]']:
        if page.locator(sel).count():
            page.locator(sel).first.fill(password)
            break

    for sel in [
        'button#login_control_continue',
        'button:has-text("Log in")',
        'button[type="submit"]',
    ]:
        if page.locator(sel).count():
            page.locator(sel).first.click()
            page.wait_for_timeout(6000)
            break


def probe_session(headless: bool | None = None) -> dict:
    """Lightweight session health check for the background worker.

    Unlike ``run_connect`` this does not print and returns a structured result,
    so callers (worker, UI) can react programmatically. Refreshes
    ``data/upwork_storage_state.json`` when authenticated.

    Returns: {"ok": bool, "reason": str, "url": str, "title": str}
    """
    from browser import browser_page

    try:
        with browser_page() as page:
            page.goto(AUTH_CHECK_URLS[0], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            ok, reason = is_logged_in(page)
            result = {
                "ok": ok,
                "reason": reason,
                "url": page.url[:120],
                "title": (page.title() or "")[:80],
            }
            if ok:
                state_path = BASE_DIR / "data" / "upwork_storage_state.json"
                page.context.storage_state(path=str(state_path))
            return result
    except ImportError:
        return {"ok": False, "reason": "playwright not installed", "url": "", "title": ""}
    except Exception as e:  # noqa: BLE001 — report, don't crash the worker
        msg = str(e).lower()
        if any(k in msg for k in ("has been closed", "already in use", "singleton")):
            hint = "profile locked — закройте окна браузера (Get-Process msedge | Stop-Process -Force) и повторите"
        else:
            hint = f"{type(e).__name__}: {str(e)[:120]}"
        return {"ok": False, "reason": f"open failed: {hint}", "url": "", "title": ""}


def run_connect(headless: bool = True, manual_login: bool = False) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Установите: pip install playwright")
        return 1

    profile_dir = project_profile_dir() if manual_login else pick_profile_dir()
    print(f"Profile: {profile_dir}")
    if "Chrome" in str(profile_dir):
        print("Hint: закройте Google Chrome перед запуском, иначе профиль занят.")

    with sync_playwright() as p:
        try:
            context = open_context(
                p, profile_dir, headless=False if manual_login else headless_mode()
            )
        except Exception as e:
            msg = str(e)
            print("Failed to open browser:", msg[:500])
            if "user data directory is already in use" in msg.lower() or "singleton" in msg.lower():
                print("Close Chrome and retry, or:")
                print("  set UPWORK_USE_CHROME_PROFILE=0")
                print("  python upwork_connect.py --login")
            return 1

        page = context.pages[0] if context.pages else context.new_page()

        try:
            if manual_login:
                print("Откроется Chrome. Войдите в Upwork, затем закройте окно браузера.")
                page.goto(
                    "https://www.upwork.com/ab/account-security/login",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                input("Нажмите Enter после входа в Upwork...")
                ok, reason = is_logged_in(page)
                print(f"Session saved to: {profile_dir}")
                print(f"Status: {'OK' if ok else 'FAIL'} ({reason})")
                context.close()
                return 0 if ok else 1

            for url in AUTH_CHECK_URLS:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                ok, reason = is_logged_in(page)
                print(f"Check {url}")
                print(f"  URL: {page.url[:100]}")
                print(f"  Title: {(page.title() or '')[:60]}")
                if ok:
                    state_path = BASE_DIR / "data" / "upwork_storage_state.json"
                    context.storage_state(path=str(state_path))
                    print(f"  Result: CONNECTED ({reason})")
                    print(f"  Session saved: {state_path}")
                    context.close()
                    return 0
                print(f"  Result: not authenticated ({reason})")

            print("Пробую логин по .env ...")
            try_auto_login(page)
            page.wait_for_timeout(5000)
            page.goto(AUTH_CHECK_URLS[0], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            ok, reason = is_logged_in(page)
            print(f"After auto-login: {'CONNECTED' if ok else 'FAIL'} ({reason})")
            print(f"  URL: {page.url[:100]}")

            if not ok:
                print()
                print("Сохраните сессию в профиль проекта:")
                print("  python upwork_connect.py --login")

            context.close()
            return 0 if ok else 1
        except Exception as e:
            print(f"Error: {type(e).__name__}: {e}")
            context.close()
            return 1


if __name__ == "__main__":
    manual = "--login" in sys.argv
    sys.exit(run_connect(manual_login=manual))
