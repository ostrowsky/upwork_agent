# Case Library

Статус: **as-built (Day 5)**.

## Goal

База кейсов (портфолио) для вставки в отклики. Только записи из БД; новые кейсы
вносит оператор вручную (содержимое — на ответственности владельца аккаунта, см. product-map).

## Scope

- CRUD кейса: title, niche, stack, description, result, budget_range, url (create/edit/delete)
- UI вкладка «Кейсы» — создание, редактирование, удаление (soft-delete при цитировании)
- Подбор 1–2 кейсов под вакансию: `cases.rank_cases(job, cases, top_n)` (детерминированный, без LLM)
- **Автогенерация синтетического кейса под вакансию** (LLM): `cases.generate_case_for_job(job, task, db)` —
  создаёт правдоподобный кейс, помечает `synthetic=1` + `job_id`, и рендерит вложение
  (PDF one-pager + PNG-инфографику, `case_artifacts.py`) в `data/case_artifacts/`.
  Триггеры: кнопка в карточке вакансии («🧪 Сгенерировать кейс под вакансию») и
  авто-фолбэк в генерации отклика при `AUTO_GEN_CASE=1`, когда в базе нет релевантного кейса.
- **Вложение PDF-кейса в отклик при сабмите**: `submit._attach_files()` загружает
  `CaseStudy.artifact_path` сгенерированных под вакансию кейсов в файловое поле формы
  отклика (`APPLY_ATTACH_INPUT`). Best-effort и не блокирует сабмит; флаг `ATTACH_CASE_PDF` (по умолч. 1).
  Ссылка/скачивание кейса также показаны в карточке вакансии в UI.

## Non-goals

- Автогенерация кейсов **без** пометки `synthetic` (фабрикация всегда помечается; ToS-ответственность на владельце аккаунта).

## User-visible behavior

- Оператор добавляет и редактирует кейсы вручную.
- При proposal видит, какие кейсы были выбраны и почему (кратко).

## Invariants

- `description` обязателен при создании и редактировании.
- Кейсы не удаляются физически, если есть Proposal с `selected_cases` → `deleted=1` (soft-delete); иначе hard-delete.
- `rank_cases` детерминирован: вес niche/stack > title > description; ничьи — по `id` asc; нет релевантных → `[]`.

## Edge cases and failure policy

- Пустая база → proposal без блока proof или с общим текстом + предупреждение в UI.

## Route / state / data implications

- Таблица `case_studies` (as-built)

## Verification mapping

- CRUD через Streamlit (create/edit/delete, soft-delete при цитировании)
- `tests/test_cases.py`: rank_cases → top 2 релевантных, niche/stack > description, пустой результат, детерминированные ничьи

## Unknowns

- Импорт кейсов из CSV.
