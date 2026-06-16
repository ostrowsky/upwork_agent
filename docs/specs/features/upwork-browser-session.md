# Upwork Browser Session

Статус: **as-built** (сессия) + **Day 2** (фоновый worker).

## Goal

Стабильная авторизованная сессия Upwork для чтения вакансий и (позже) отправки откликов без повторного логина каждый раз, обслуживаемая **отдельным фоновым процессом** (не внутри Streamlit).

## Scope

- **`browser.browser_page()`** — единый контекст-менеджер сессии (Playwright + open_context + page + гарантированный close). Все браузерные операции (probe, scrape, submit, send, import, sync, capture, healthcheck) идут через него — один источник правды. Исключения: `run_connect` (интерактивный первый логин) и `check_upwork` (отдельный диагностический браузер).
- Профиль Playwright: `data/upwork_profile`
- Скрипт проверки: `upwork_connect.py` (CLI) + `probe_session()` (программный health-check)
- Фоновый процесс: `worker.py` — периодический heartbeat + проверка сессии
- Статус-файл: `data/worker_status.json` (worker пишет, UI читает read-only)
- Переменные: `UPWORK_EMAIL`, `UPWORK_PASSWORD`, `UPWORK_BROWSER_CHANNEL`, `UPWORK_HEADLESS`, `UPWORK_USE_CHROME_PROFILE`, `WORKER_INTERVAL`
- Браузер (`UPWORK_BROWSER_CHANNEL`): `chrome` | `msedge` | `chromium`. Edge не конфликтует с повседневным Chrome (рекомендуется). Профиль на каждый канал отдельный: `chrome` → `data/upwork_profile`, прочие → `data/upwork_profile_<channel>` (логин делается один раз на каждый браузер).
- Экспорт `data/upwork_storage_state.json` после успешного connect/probe

## Worker (Day 2)

- Запуск: `python worker.py` (loop) или `python worker.py --once` (один тик).
- Каждый тик: определить активную задачу → `probe_session()` → записать heartbeat в `data/worker_status.json`.
- Поля статуса: `state` (starting/running/stopped), `heartbeat`, `active_task`, `session_ok`, `session_reason`, `pid`, `interval`, `updated_at`.
- Интервал: `WORKER_INTERVAL` секунд (по умолчанию 300). Сон нарезан по 1с — Ctrl+C отзывчив.
- Тик никогда не роняет цикл: ошибки probe/DB перехватываются и пишутся в `session_reason`.
- Ingestion/qualification (Дни 3–4) подключаются в `tick()` без смены контракта статуса.
- **Автономный полный пайплайн (tick):** probe → ingest → qualify → draft → (submit при `AUTO_SUBMIT=1`) → import messages (`WORKER_IMPORT_MESSAGES=1`) → daily report → anomaly alerts. Браузерная фаза под `browser_lock`.
- **Логирование:** все действия в `data/worker.log` (ротация) + stdout (UTF-8 reconfigure — иначе cp1251 крашит non-ASCII). `safe_tick` оборачивает тик: трейсбек в лог, `last_error` в статус, цикл не падает.

## Non-goals

- Параллельная работа с открытым обычным Chrome на том же User Data (конфликт профиля)
- Гарантия работы в headless при активном Cloudflare

## User-visible behavior

- Оператор один раз логинится (`python upwork_connect.py --login` или авто через .env).
- `python upwork_connect.py` выводит CONNECTED и путь к сохранённой сессии.
- При истечении сессии — redirect на login; оператор повторяет connect.

## Invariants

- По умолчанию `UPWORK_HEADLESS=0` (видимый Chrome) из-за Cloudflare.
- Профиль проекта (`UPWORK_USE_CHROME_PROFILE=0`) — основной для агента.
- Пароль только в `.env`, не в SQLite.
- Долгий цикл автоматизации — **только в `worker.py`**, не в Streamlit (правило AGENTS.md).
- UI взаимодействует с worker лишь через статус-файл (read-only), без запуска браузера из Streamlit.
- Активной может быть ≤ 1 задачи; worker обслуживает именно её (см. `companies-and-tasks`).

## Edge cases and failure policy

| Ситуация | Поведение |
|----------|-----------|
| Cloudflare «Один момент…» | Не считать CONNECTED; повтор с headless=0 |
| Chrome User Data занят | Сообщение «закройте Chrome» |
| Неверный пароль | Остаётся на login URL |
| 2FA | INCONCLUSIVE; оператор завершает вручную в открытом браузере |

## Route / state / data implications

- Check URLs: `/nx/find-work/`, `/nx/proposals/`, `/nx/find-work/best-matches`
- Logged in = URL без `account-security/login` и title без login/challenge/момент

## Verification mapping

- `python upwork_connect.py` exit code 0
- `tests/test_worker.py`: write/read статуса round-trip; `tick()` пишет heartbeat и поля сессии (с замоканным `probe_session`); цикл не падает при ошибке probe.
- Ручной: `python worker.py --once` → создаётся/обновляется `data/worker_status.json` с `session_ok`; UI-сайдбар показывает статус и heartbeat.
- Ручной: best-matches открывается без login redirect.

## Unknowns

- Частота re-auth; нужен ли cookie refresh job.
- Нужен ли авто-рестарт worker (supervisor) в проде — пока запуск вручную.
