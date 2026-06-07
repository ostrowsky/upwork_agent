# Spec Backlog

| Spec | Приоритет | Статус | Зависимости |
|------|-----------|--------|-------------|
| [platform-overview](features/platform-overview.md) | P0 | draft | — |
| [upwork-browser-session](features/upwork-browser-session.md) | P0 | **as-built** + worker (Day 2) | — |
| [companies-and-tasks](features/companies-and-tasks.md) | P0 | **as-built** (Day 1) | platform-overview |
| [job-ingestion-and-qualification](features/job-ingestion-and-qualification.md) | P0 | **as-built** (ingest Day 3, qualify Day 4) | companies-and-tasks, upwork-browser-session |
| [proposal-generation](features/proposal-generation.md) | P0 | **as-built** (gen Day 6, submit Day 7) | job-ingestion, case-library |
| [case-library](features/case-library.md) | P1 | **as-built** (Day 5) | — |
| [agent-questions-and-learning](features/agent-questions-and-learning.md) | P1 | **as-built** (Day 9) | companies-and-tasks |
| [client-conversations](features/client-conversations.md) | P1 | **as-built** (Day 8) | upwork-browser-session |
| [daily-reporting](features/daily-reporting.md) | P2 | **as-built** (Day 10) | job-ingestion |
| [proposal-status-sync](features/proposal-status-sync.md) | P1 | **as-built** (gap #1) | proposal-generation |
| [browser-concurrency-lock](features/browser-concurrency-lock.md) | P1 | **as-built** (gap #3) | upwork-browser-session |
| Web QA pack (`qa/web/`) | P3 | not started | optional per bootstrap |

**Правило:** статус `as-built` = поведение задокументировано по факту кода; `draft` = контракт на реализацию.

## Тех-долг (production-readiness)

| # | Пункт | Статус |
|---|-------|--------|
| 1 | Ставка под вакансию (из бюджета/сметы, не фикс $35) | ✅ `submit.compute_hourly_rate/compute_fixed_amount` + тесты |
| 2 | Network-based ingest (стабильный парсинг вместо DOM) | ✅ Лента SSR'ится в `window.__NUXT__.state.feedBestMatch.jobs`; `jobs.scrape_best_matches_nuxt`/`parse_nuxt_jobs` — основной путь, DOM-скрейп как fallback. Проверено на 30 реальных вакансиях |
| 3 | Детекция аномалий worker + алерты | ✅ `worker.detect_anomalies` + поле `anomalies`; алерты в Telegram/Discord при НОВЫХ аномалиях (`reporting.send_alert`), Day 10 |
| 4 | Обработка финальной модалки подтверждения сабмита | ✅ `APPLY_CONFIRM_BUTTON` + тест |
| 5 | Селектор-канарейка (дрейф верстки) | ✅ `healthcheck.py` (`--apply`) + тесты логики отчёта |

Селекторы Upwork централизованы в `upwork_selectors.py` (single source of truth, фолбэки + даты verified).
