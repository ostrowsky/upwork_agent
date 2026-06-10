# Job Ingestion and Qualification

Статус: **ingest — as-built (Day 3)**; **qualify — as-built (Day 4)**.

## Goal

Получать вакансии из Upwork и автоматически решать: **откликаться** или **skip**, по стратегии активной задачи.

## Scope

- Импорт: ручной (paste/URL) в UI + автопарсинг best-matches в worker (`jobs.scrape_best_matches`)
- Дедуп по `upwork_job_id` и `source_url` (в т.ч. внутри одного батча)
- Статусы Job: `NEW` → `QUALIFIED` → `SKIPPED` | `READY_TO_PROPOSE`
- LLM qualify (Day 4): fit_score, risk_score, ai_decision (`APPLY`|`SKIP`), skip_reason, ai_analysis_json
- UI: список, фильтр по статусу, обоснование AI, ручной override APPLY/SKIP

## Qualify (Day 4, as-built)

- Модуль `qualify.py` — общий для worker и UI; connects здесь НЕ тратятся (это submit, Day 7).
  - `build_qualify_messages(task, job)` — system+user промпт со стратегией активной задачи (инвариант: стратегия в каждом вызове).
  - `parse_qualify_response(text)` — устойчивый парс JSON (plain / ```fenced``` / в прозе), валидация decision, кламп scores 0–100; невалид → ValueError.
  - `qualify_job(job, task, db)` — ретрай 1 раз при невалидном JSON, иначе `status=ERROR`; APPLY→`READY_TO_PROPOSE`, SKIP→`SKIPPED`; пишет `ai_analysis_json`.
  - `qualify_new_jobs(task_id, limit)` — батч по `NEW`; `task_id=None` → все NEW, каждая по стратегии СВОЕЙ задачи; возвращает `{applied, skipped, errors, total}`.
- Worker `tick()`: после ingest квалифицирует NEW (LLM, без браузера); счётчики в `last_qualify_*`.
- UI: кнопки «Квалифицировать NEW» (активная задача) и «Все задачи», фильтр статусов, ручной override (`override_job_decision`).
- Бюджет берётся из `data-test="job-type"` (чистая строка: `Fixed-price` / `Hourly: $15-$30`); элементы `budget`/`formatted-amount` НЕ использовать (фрагментированы / хранят спенд клиента).

## Ingest (Day 3, as-built)

- Модуль `jobs.py` — общий для worker и UI.
  - `normalize_record` — чистка записи, требует title+description, выводит `upwork_job_id` из URL.
  - `ingest_jobs(records, task_id)` — вставка `NEW` с дедупом; идемпотентно при повторном скрейпе; возвращает `{added, skipped_dup, invalid}`.
  - `scrape_best_matches_nuxt(limit)` — **основной путь**: читает структурные вакансии из `window.__NUXT__.state.feedBestMatch.jobs` (`parse_nuxt_jobs`), устойчив к смене верстки (ломается только при смене схемы данных).
  - `scrape_best_matches(limit)` — **fallback**: DOM-скрейп по селекторам (при сбое → дамп + reason).
  - `ingest_from_upwork(task_id, limit)` — worker-entrypoint: nuxt → (fallback DOM) → ingest.
  - `scrape_search(query, limit)` / `ingest_search(query, task_id, limit)` — **поиск по ключам** (gap #14): авто-локация массива результатов в `window.__NUXT__` (`_array_at_best_path`), DOM-fallback. UI: «🔎 Поиск вакансий по ключевым словам».
- Worker `tick()`: ingest только при `session_ok` И наличии активной задачи; счётчики в `worker_status.json` (`last_ingest_added/skipped/reason`).
- UI ручной импорт идёт через тот же `ingest_jobs` (дубликат → warning).
- UI «⬇️ Импортировать из Upwork» — **разовый** скрейп `ingest_from_upwork` для активной задачи (одно действие, не цикл; блокирует UI на время браузера; нельзя одновременно с worker — конфликт профиля).

## Non-goals

- 100% автосабмит без оператора в MVP
- Парсинг всей ленты 24/7 без лимитов connects

## User-visible behavior

- Мусорные проекты ($100, NFT, вне ниши) → SKIP с понятной причиной на русском/EN.
- Подходящие → APPLY, статус READY, fit_score виден в UI.
- Оператор может вручную переопределить решение AI.
- Worker (to-be) обрабатывает NEW пакетами с дневным лимитом.

## Invariants

- Решение qualify всегда логируется (JSON анализ в БД).
- SKIP не создаёт Proposal.
- Стратегия активной задачи (канонический `task.strategy`) передаётся в каждый LLM-вызов qualify.
- **СТРОГАЯ квалификация (жёсткие правила, нарушение → SKIP):**
  - **Стек**: основной движок/стек вакансии должен совпадать со стеком стратегии. Смежность темы
    («3D/игры») ≠ совпадение стека. Напр. при стратегии Unity вакансии на Unreal / Three.js /
    Babylon.js / WebGL / native — SKIP.
  - **Бюджет** вне диапазона стратегии — SKIP.
  - Исключения стратегии (`без NFT/gambling`) — буквально.
- Канон стратегии меняется через форму «✏️ Редактировать задачу» **или** инструмент агента
  `update_strategy` (правки в чате-обсуждении сами по себе НЕ меняют канон).

## Edge cases and failure policy

| Ситуация | Поведение |
|----------|-----------|
| Нет бюджета в описании | qualify по тексту; risk выше |
| LLM invalid JSON | retry 1 раз → статус ERROR |
| Дубликат URL | не создавать второй Job |
| Cloudflare при ingest | пауза, алерт оператору |

## Route / state / data implications

- Расширение `Job`: `task_id`, `upwork_job_id`, `outcome` (later)
- Индекс на `source_url`

## Verification mapping

- Ingest (Day 3): `tests/test_ingest.py` — дедуп по `upwork_job_id`/`source_url`, внутри-батч дедуп, отказ пустых записей, вывод job_id из URL.
- Селекторы скрейпа: `tests/test_scrape_selectors.py` на фикстуре `tests/fixtures/feed_sample.html` (разметка сверена с живой best-matches 2026-06: тайл = `section.air3-card-section.air3-card-hover`, ссылка = `h3.job-tile-title a`). При 0 тайлах worker дампит `data/feed_debug.html` + `.png`.
- Ручной: `worker.py --once` при активной задаче пишет `last_ingest_*` в статус.
- Qualify (Day 4): 3 тестовых описания → 1 SKIP, 2 APPLY с reason; pytest на парсер JSON ответа qualify.

## Unknowns

- Точные пороги fit_score для автоматического READY.
