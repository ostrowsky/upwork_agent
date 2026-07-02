# SPA Apply Navigation (Cloudflare bypass on datacenter IPs)

Статус: **as-built**.

## Goal

Отправлять отклики с сервера (датацентровый IP), не упираясь в Cloudflare.

## Проблема (доказано экспериментально)

Upwork/Cloudflare отдаёт **непроходимую заставку «Just a moment»** на *полностраничную загрузку*
(`page.goto`) страниц `/jobs/...` и `/nx/proposals/.../apply/` с датацентрового IP. При этом:

- **Лента** `/nx/find-work/best-matches` проходит Cloudflare с того же IP;
- смена IP не помогает: KZ residential/mobile-прокси (даже с реальным Chrome, залогиненным
  профилем и stealth) всё равно ловят заставку на `goto(apply)`;
- дело **не в IP, а в deep-linking**: клиентская навигация внутри SPA грузит контент XHR-запросами,
  переиспользуя `cf_clearance` ленты, — свежий challenge не выдаётся. Именно так ходит человек.

## Реализация (`submit.py`)

- `apply_via_spa_enabled()` — флаг `APPLY_VIA_SPA` (по умолчанию `1`).
- `_spa_job_route(job)` — клиентский маршрут детали `"/best-matches/details/~<upwork_job_id>"`
  (относительно базы роутера `/nx/find-work/`).
- `_navigate_apply_spa(page, job)`:
  1. `goto(FEED_URL)` → лента (проходит Cloudflare);
  2. `window.$nuxt.$router.push('/best-matches/details/~<id>')` — **клиентский** переход к
     конкретной вакансии (без полной перезагрузки → без challenge, подтверждено `cf=False`);
  3. `_dismiss_profile_modal()` — снять полноэкранную модалку «Complete your profile»;
  4. реальный клик по кнопке **«Apply now»** → форма отклика;
  5. успех = появился cover-letter / URL содержит `/apply`.
- `_open_apply_page(page, url, job)` — SPA-навигация, при неудаче **фолбэк** на старый
  deep-link `goto(url)`. `_apply_on_page` вызывает его вместо прямого `goto`.
- На fake-page (без `get_by_role`) SPA-путь возвращает False → фолбэк, поэтому юнит-тесты
  формы не затрагиваются.

## Ограничения / предпосылки

- **Профиль аккаунта должен быть заполнен на 100%** — иначе Upwork показывает полноэкранную
  модалку «Complete your profile», перекрывающую «Apply now» (это гейт Upwork, не Cloudflare).
- Прокси **не требуется** — навигация проходит с датацентрового IP.
- Полагается на Nuxt 2 `window.$nuxt.$router`; при смене SPA-стека Upwork — обновить маршрут/базу
  (текущие: base `/nx/find-work/`, route `/best-matches/details/~<id>`).

## Тесты

`tests/test_spa_apply_nav.py`: флаг вкл/выкл, построение маршрута (`~`, пустой id),
возврат False на fake-page, фолбэк `_open_apply_page` на deep-link.
