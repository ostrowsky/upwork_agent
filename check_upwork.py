"""Проверка .env и доступности Upwork (без вывода секретов)."""
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv()


def env_ok() -> bool:
    email = os.getenv("UPWORK_EMAIL", "").strip()
    password = os.getenv("UPWORK_PASSWORD", "").strip()
    print(f"UPWORK_EMAIL: {'ok' if email else 'MISSING'}")
    print(f"UPWORK_PASSWORD: {'ok' if password else 'MISSING'}")
    return bool(email and password)


def check_http() -> None:
    import httpx

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    url = "https://www.upwork.com/ab/account-security/login"
    try:
        r = httpx.get(url, follow_redirects=True, timeout=25.0, headers=headers)
        title = ""
        m = re.search(r"<title[^>]*>([^<]+)</title>", r.text, re.I)
        if m:
            title = m.group(1).strip()
        print(f"HTTP {url}")
        print(f"  status: {r.status_code}")
        print(f"  title: {title or '(n/a)'}")
        if r.status_code == 403 and "challenge" in title.lower():
            print("  note: Cloudflare challenge — нужен реальный браузер (Playwright).")
    except Exception as e:
        print(f"HTTP FAIL: {e}")


def check_browser_login() -> None:
    email = os.getenv("UPWORK_EMAIL", "").strip()
    password = os.getenv("UPWORK_PASSWORD", "").strip()
    if not email or not password:
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed.")
        return

    login_url = "https://www.upwork.com/ab/account-security/login"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:
            print(f"Browser launch failed: {e}")
            print("Free ~2 GB disk or run: playwright install chromium")
            return

        page = browser.new_page()
        try:
            page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            title = page.title()
            print(f"Browser login page title: {title}")

            if "challenge" in title.lower() or "just a moment" in title.lower():
                print("Login check: BLOCKED by Cloudflare (captcha/challenge).")
                print("Откройте Upwork вручную в Chrome — credentials в .env для будущего скрипта.")
                return

            # Upwork UI меняется; пробуем типичные селекторы
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

            for sel in ['button#login_control_continue', 'button:has-text("Log in")', 'button[type="submit"]']:
                if page.locator(sel).count():
                    page.locator(sel).first.click()
                    page.wait_for_timeout(5000)
                    break

            final_url = page.url
            final_title = page.title()
            print(f"After submit URL: {final_url[:100]}")
            print(f"After submit title: {final_title[:80]}")

            if "account-security/login" not in final_url and "challenge" not in final_title.lower():
                print("Login check: LIKELY OK (left login page).")
            elif "verify" in final_url.lower() or "security" in final_title.lower():
                print("Login check: 2FA / verification required.")
            else:
                print("Login check: INCONCLUSIVE — проверьте вручную.")
        except Exception as e:
            print(f"Browser check error: {type(e).__name__}: {e}")
        finally:
            browser.close()


if __name__ == "__main__":
    ok = env_ok()
    print()
    check_http()
    print()
    if ok and "--browser" in sys.argv:
        check_browser_login()
    elif ok:
        print("Для проверки логина: python check_upwork.py --browser")
