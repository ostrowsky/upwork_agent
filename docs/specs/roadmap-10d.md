# Roadmap — 10-Day MVP (spec-first)

Подход: [spec-first-bootstrap](https://github.com/ostrowsky/spec-first-bootstrap).
Каждый день: **спека (`draft` → контракт) → код по контракту → верификация (тест/чеклист)**.
Порядок следует зависимостям из [spec-backlog](spec-backlog.md) (P0 → P1 → P2).

## Зафиксированные решения заказчика (2026-06-04)

См. [product-map.md](product-map.md) → «Решения заказчика». Кратко:
автосабмит **включён**; один аккаунт Upwork (`.env`); отклики **EN+RU**;
кейсы только из базы; фабрикация опыта допускается под ответственность заказчика; стек Python.

## План

| День | Спека | Что делаем | Verify |
|------|-------|-----------|--------|
| 1 | `companies-and-tasks` | DB: Company/Task/TaskMessage, `task_id` у Job; UI CRUD + чат-стратегия задачи; выбор активной задачи | `tests/test_companies_and_tasks.py` + ручной чеклист спеки |
| 2 | `upwork-browser-session` (as-built) | Вынести фоновый worker отдельным процессом; пинг сессии; первый логин | `python upwork_connect.py` логинится; worker стартует |
| 3 | `job-ingestion-and-qualification` (ingest) | Парсинг ленты Upwork → Job(NEW), дедуп, ручной импорт fallback | тест дедупа; ingest пишет в БД |
| 4 | `job-ingestion-and-qualification` (qualify) | LLM APPLY/SKIP по стратегии задачи, лимиты connects, gating, skip-reason | тест классификатора на наборе вакансий |
| 5 | `case-library` (partial → done) | Довести CRUD кейсов, привязка к задаче; только из базы | тест выборки кейсов под нишу |
| 6 | `proposal-generation` (генерация) | 7-блочный шаблон (понимание→кейсы→подход→вопросы→риски→next step), EN/RU, подбор кейсов, estimate | тест структуры письма |
| 7 | `proposal-generation` (автосабмит) | Submit через браузерную сессию, списание connects, статус SENT, идемпотентность | e2e в тестовом режиме (dry-run флаг) |
| 8 | `client-conversations` | Карточка клиента на каждый контакт, AI-draft ответа, ручной override | тест создания карточки + draft |
| 9 | `agent-questions-and-learning` | WIN/LOST, анализ проигрышей, вопросы агенту, метрики (connects/replies/interviews/hires/revenue) | тест агрегации метрик |
| 10 | `daily-reporting` + стабилизация | Реальная отправка в Telegram/Discord, история отчётов; прогон полного flow; статусы спек `draft`→`as-built` | отчёт уходит; e2e онбординг→report |

## Definition of Done (на каждую фичу)

1. Спека обновлена в том же изменении, что и код (правило AGENTS.md).
2. Код реализует контракт спеки; инварианты соблюдены.
3. Есть верификация: pytest или ручной чеклист из секции «Verification mapping» спеки.
4. Статус в `spec-backlog.md` обновлён.
