# Gaps Review & Resolution

Пострелизный аудит. Статус каждого пробела.

| # | Пробел | Приоритет | Статус |
|---|--------|-----------|--------|
| 1 | Fixed-price форма отклика | 🔴 | ✅ логика (compute_fixed_amount из сметы) + тест; **live-подгонка формы — по dry-run дампу** |
| 2 | `send_message` без мок-тестов | 🔴 | ✅ рефактор `_compose_and_send` + 5 fake-page тестов |
| 3 | Конфликт worker↔UI за профиль Edge | 🔴 | ✅ `browser_lock` + интеграция в worker и все UI-браузерные операции + тесты + спека |
| 4 | Connects-баланс | 🔴 | ✅ читается с формы отклика при сабмите (`connects.parse_connects_balance`), хранится, виден на Dashboard + в отчёте; тест |
| 5 | Outcome без авто-детекта из Upwork timeline | 🟡 | 🔶 стейт стадий lazy-loaded + матчинг job↔proposal нетривиален; «Replies» уже авто; ручной outcome работает. Probe с reload готов для будущего захвата |
| 6 | UTC vs локаль в дате отчёта | 🟡 | ✅ `reporting.utc_today()` везде |
| 7 | `rank_cases` цитирует слабые кейсы | 🟡 | ✅ `min_score` (env `CASE_MIN_SCORE=4`) + тест |
| 8 | Исчезающий фидбэк в UI (rerun) | 🟡 | ✅ persist в session_state для submit/batch/sync/qualify |
| 9 | Дедуп клиентов | 🟡 | ☑️ адекватно: матч по `external_id`(roomId)/name; для MVP достаточно |
| 10 | Healthcheck не на расписании | 🟢 | ☑️ покрыто anomaly-алертами worker; `healthcheck.py` доступен вручную/cron |
| 11 | Нет backoff на rate-limit LLM | 🟢 | ✅ экспоненциальный retry в `ai.call_llm` + env + тест |
| 12 | LLM-вызовы не считаются | 🟢 | ✅ `ai.LLM_STATS` (calls/retries/errors) → в `worker_status.json` |
| 13 | Примитивный language-detect | 🟢 | ☑️ адекватно для RU/EN (требование ТЗ) |
| 14 | Только best-matches, без поиска по ключам | 🟢 | ✅ `jobs.scrape_search`/`ingest_search` (NUXT авто-поиск массива + DOM fallback) + UI «🔎 Поиск» + тесты |
| 15 | Миграции без Alembic | 🟢 | ⏳ post-MVP при росте схемы; есть тест миграций на существующей БД |

Легенда: ✅ закрыто · ☑️ признано достаточным · 🔶 нужен live-захват · ⏳ отложено (post-MVP).
