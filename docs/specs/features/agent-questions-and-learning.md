# Agent Questions and Learning

Статус: **as-built (Day 9)**.

## Goal

Агент задаёт уточняющие вопросы оператору для лучших откликов и учится на проигрышах.

## Реализация (Day 9)

- БД: `Job.outcome` (REPLIED/INTERVIEW/WIN/LOST), `Job.loss_reason`, `Job.revenue`; таблица `agent_questions`.
- `analytics.py`: `compute_metrics` (воронка: connects/replies/interviews/hires/revenue), `analytics_by_bucket` (по типу бюджета, флаг неэффективных категорий при ≥N отправок и 0 наймов).
- `learning.py`: `set_outcome` (WIN пишет revenue; LOST → `analyze_loss`), `analyze_loss` (LLM post-mortem → `loss_reason` + вопрос агенту), CRUD вопросов; `answer_question` пишет ответ в `task_messages` (инвариант).
- UI: метрики воронки + таблица по категориям на Dashboard; кнопки исхода (Ответил/Интервью/Проигрыш/Найм+revenue) на карточке вакансии; вкладка «Вопросы агенту».

## Scope

- Таблица `agent_questions`: текст, статус open/answered, связь с task_id
- UI: отдельная вкладка «Вопросы агенту»
- При `Job.outcome = LOST`: LLM post-mortem → причина, рекомендации в notes задачи

## Non-goals

- Автономное изменение стратегии без подтверждения оператора

## User-visible behavior

- LLM создаёт вопрос, когда не хватает данных (ниша, ставка, портфолио).
- Оператор отвечает в UI → ответ попадает в контекст задачи.
- После проигрыша видна карточка «почему проиграли» и что изменить в стратегии.

## Invariants

- Ответы на вопросы сохраняются в `task_messages` или linked table.
- Loss analysis не удаляется при редактировании job.

## Edge cases and failure policy

- Оператор игнорирует вопросы → агент использует дефолты из ТЗ задачи.

## Route / state / data implications

- `agent_questions`, `Job.outcome`, `Job.loss_reason` (to-be)

## Verification mapping

- `tests/test_learning.py`: WIN пишет revenue; невалидный outcome; LOST → loss_reason + вопрос; ответ попадает в task_messages; парс JSON.
- `tests/test_analytics.py`: воронка (connects/replies/hires/revenue); флаг неэффективной категории.

## Unknowns

- Автоматически ли поднимать вопрос после N подряд LOST в одной нише.
