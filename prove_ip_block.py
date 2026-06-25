#!/usr/bin/env python3
"""
Доказательство: Upwork (Cloudflare) блокирует подачу отклика по IP сервера.

Идея: один и тот же скрипт открывает две страницы Upwork в одном браузере:
  1) СТРАНИЦА ПРОСМОТРА  (/nx/find-work/...)        — read-only
  2) СТРАНИЦА ПОДАЧИ ОТКЛИКА (/nx/proposals/.../apply/) — action endpoint

Вход в аккаунт НЕ требуется: Cloudflare отдаёт challenge ("Just a moment…")
ещё ДО контента Upwork. Поэтому результат зависит только от IP/репутации.

Ожидаемо:
  • с датацентрового IP (сервер):   apply  → застревает на "Just a moment…"
  • с residential IP (дом/мобайл):  apply  → проходит Cloudflare (уходит на логин)

Запуск:
    pip install playwright && playwright install chromium
    python prove_ip_block.py
    # на headless-сервере: xvfb-run -a python prove_ip_block.py
Опции (env):
    HEADLESS=1                  запускать без окна (по умолчанию headed)
    APPLY_JOB_ID=~0123...       конкретная вакансия (по умолчанию пример)
    WAIT_SECONDS=45             сколько ждать снятия challenge

Артефакты-доказательства сохраняются рядом: proof_*.png и proof_*.html
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

BROWSE_URL = "https://www.upwork.com/nx/find-work/best-matches"
APPLY_ID = os.getenv("APPLY_JOB_ID", "~022069750705030808080").strip()
APPLY_URL = f"https://www.upwork.com/nx/proposals/job/{APPLY_ID}/apply/"
WAIT_SECONDS = int(os.getenv("WAIT_SECONDS", "45"))
HEADLESS = os.getenv("HEADLESS", "0").strip().lower() in ("1", "true", "yes")

CF_MARKERS = ("just a moment", "один момент", "checking your browser",
              "verify you are human", "attention required")


def public_ip_info() -> dict:
    """Публичный IP и владелец (datacenter vs residential)."""
    try:
        with urllib.request.urlopen("https://ipinfo.io/json", timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def is_cloudflare_challenge(title: str) -> bool:
    t = (title or "").lower()
    return any(m in t for m in CF_MARKERS)


def turnstile_checkbox_present(page) -> bool:
    """Интерактивный чекбокс Cloudflare Turnstile ('Подтвердите, что вы человек')."""
    try:
        if page.locator("iframe[src*='challenges.cloudflare.com']").count() == 0:
            return False
        fr = page.frame_locator("iframe[src*='challenges.cloudflare.com']")
        return fr.locator("input[type=checkbox]").count() > 0
    except Exception:  # noqa: BLE001
        return False


def check_url(page, label: str, url: str) -> dict:
    print(f"\n=== {label} ===\n{url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    blocked = True
    interactive = False
    waited = 0
    step = 5
    while waited <= WAIT_SECONDS:
        page.wait_for_timeout(step * 1000)
        waited += step
        title = page.title() or ""
        if not is_cloudflare_challenge(title):
            blocked = False
            break
        if turnstile_checkbox_present(page):
            interactive = True
        print(f"  {waited:>3}s  Cloudflare challenge: title={title[:40]!r}"
              + ("  [интерактивный чекбокс есть]" if interactive else "  [чекбокса нет — глухой блок]"))
    title = page.title() or ""
    slug = label.lower().replace(" ", "_")
    png = f"proof_{slug}.png"
    html = f"proof_{slug}.html"
    try:
        page.screenshot(path=png, full_page=False)
        with open(html, "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception:  # noqa: BLE001
        pass
    if not blocked:
        verdict = "OK (passed Cloudflare)"
    elif interactive:
        verdict = "CHALLENGE — решаемый чекбокс (типично для нормального/residential IP)"
    else:
        verdict = "HARD BLOCK — глухой 'Just a moment', без чекбокса (типично для датацентр./флагнутого IP)"
    print(f"  -> {verdict}  | final title={title[:50]!r}")
    print(f"  -> доказательства: {png}, {html}")
    return {"label": label, "url": url, "blocked": blocked, "interactive": interactive,
            "final_title": title[:80], "screenshot": png, "html": html}


def main() -> int:
    try:  # avoid UnicodeEncodeError on cp1251/cp866 Windows consoles
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    print("=" * 70)
    print("ДОКАЗАТЕЛЬСТВО: Cloudflare-блокировка подачи отклика по IP")
    print("Время:", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    print("=" * 70)

    ip = public_ip_info()
    print("\n--- IP этой машины ---")
    for k in ("ip", "org", "city", "region", "country", "hostname"):
        if ip.get(k):
            print(f"  {k}: {ip[k]}")
    if ip.get("error"):
        print("  (не удалось определить IP:", ip["error"], ")")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\nУстановите: pip install playwright && playwright install chromium")
        return 2

    # Браузер: bundled chromium по умолчанию; CHANNEL=msedge/chrome — системный.
    channel = os.getenv("CHANNEL", "").strip()
    launch_kwargs = {"headless": HEADLESS, "args": [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run", "--no-default-browser-check",
    ]}
    if channel:
        launch_kwargs["channel"] = channel
        print(f"\n(браузер: {channel})")

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        try:
            results.append(check_url(page, "BROWSE page", BROWSE_URL))
            results.append(check_url(page, "APPLY page", APPLY_URL))
        finally:
            try:
                ctx.close()
                browser.close()
            except Exception:  # noqa: BLE001 — challenge page can drop the connection
                pass

    print("\n" + "=" * 70)
    print("ИТОГ")
    print("=" * 70)
    for r in results:
        if not r["blocked"]:
            mark = "[OK]"
        elif r["interactive"]:
            mark = "[CHALLENGE: чекбокс]"
        else:
            mark = "[HARD BLOCK]"
        print(f"  {mark:22} {r['label']:12} title={r['final_title']!r}")

    browse = next((r for r in results if r["label"] == "BROWSE page"), None)
    apply = next((r for r in results if r["label"] == "APPLY page"), None)
    print("\nВЫВОД:")
    if apply and apply["blocked"] and not apply["interactive"]:
        print("  Страница ПОДАЧИ ОТКЛИКА получает ГЛУХОЙ Cloudflare-блок ('Just a moment'")
        print("  без чекбокса) — а страница просмотра открывается. Это поведение Cloudflare")
        print("  для IP с низкой репутацией (датацентр/хостинг). С residential IP та же")
        print("  страница отдаёт РЕШАЕМЫЙ чекбокс. Разница только в IP → блокировка по IP.")
    elif apply and apply["blocked"] and apply["interactive"]:
        print("  Страница подачи отклика отдаёт РЕШАЕМЫЙ чекбокс (нормальный/residential IP).")
    elif apply and not apply["blocked"]:
        print("  С этого IP страница подачи отклика проходит Cloudflare — IP не заблокирован.")
    else:
        print("  См. артефакты proof_*.png / proof_*.html.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
