# Proposal Generation

Статус: **генерация — as-built (Day 6)**; **автосабмит — as-built (Day 7, dry-run по умолчанию)**.

## Автосабмит (Day 7)

- Модуль `submit.py` (чистая логика-гард тестируема; браузерная часть изолирована).
  - `build_apply_url(job)` — `/nx/proposals/job/~{id}/apply/` из `upwork_job_id`/`source_url`.
  - `can_submit(job, proposal)` — идемпотентность: не слать, если `SENT`, не-DRAFT, пустой текст, нет apply-url.
  - `submit_proposal(job, proposal, db, dry_run)` — **dry-run по умолчанию** (заполняет cover letter, скриншот `apply_debug.*`, НЕ кликает Send). LIVE только при `AUTO_SUBMIT=1`: клик Send → `Proposal.status=SENT`, `submitted_at`, `connects_spent`; `Job.status=SENT`.
  - `submit_ready(task_id)` — батч по `PROPOSAL_DRAFTED`; режется `SUBMIT_PER_RUN` (по умолчанию **1** сабмит за прогон) и дневным `DAILY_SUBMIT_LIMIT`.
- Worker `tick()`: сабмит ТОЛЬКО при `AUTO_SUBMIT=1`; счётчики `last_submit_*`.
- UI: на черновике кнопка «🚀 Отправить отклик» (режим LIVE/DRY-RUN по `AUTO_SUBMIT`); статус `SENT` → бейдж.
- Env: `AUTO_SUBMIT`, `SUBMIT_PER_RUN`, `DAILY_SUBMIT_LIMIT`.
- Селекторы формы (сверено 2026-06, dry-run): cover letter `textarea[aria-labelledby="cover_letter_label"]`; ставка `input#step-rate` (обязательна, иначе Send недоступен); кнопка `button.air3-btn-primary:has-text("Send for")`; connects из текста «Send for N Connects». Deep-link проходит через ожидание Cloudflare.
- Ставка из `BID_HOURLY_RATE`/`BID_FIXED_AMOUNT` — **упрощение MVP** (единая ставка); TODO: выводить из сметы под вакансию.
- Dry-run подтверждён: `filled=True, bid=35, connects=16`. Live (`AUTO_SUBMIT=1`) — кликает Send, не проверялся вживую (требует реальной траты connects).

## Goal

Автоматически готовить отклик, максимально повышающий шанс выбора, по фиксированному шаблону заказчика.

## Реализация (Day 6)

- Модуль `proposals.py` (общий для worker и UI).
  - `detect_language(text)` — RU при преобладании кириллицы, иначе EN.
  - `rank_cases` (из `cases.py`) выбирает 1–2 реальных кейса; proof цитирует только их.
  - `build_proposal_messages` → LLM возвращает JSON по 7 блокам.
  - `parse_proposal_response` — устойчивый парс + проверка наличия всех ключей (REQUIRED_KEYS); ретрай 1 раз → ERROR.
  - `assemble_proposal` — собирает финальный текст; `estimate` хранится отдельно.
  - `generate_proposal(job, task, db)` — **идемпотентно**: один активный DRAFT на job; ставит `Job.status=PROPOSAL_DRAFTED`, пишет `selected_cases`.
  - `generate_drafts_for_ready(task_id, limit)` — батч по READY_TO_PROPOSE.
- Worker `tick()`: после qualify генерит черновики (LLM, без браузера); счётчики `last_draft_*`.
- UI: на READY/DRAFTED джобе — «Сгенерировать/Перегенерировать отклик», превью + ручное редактирование текста и сметы.

## Scope

- Генерация текста Proposal в БД (`proposals`)
- Структура письма (7 блоков):
  1. First line — доказать, что прочитали задачу
  2. Relevant proof — 1–2 кейса из базы
  3. Approach — как начнём
  4. Clarifying questions — 2–4 вопроса
  5. Risk reducer
  6. CTA — next step в Upwork
  7. Детализированная смета (если уместно)
- Подбор кейсов из `CaseStudy` (релевантность по niche/stack/budget)
- UI: preview, edit, approve → `SENT` / `SENT_MANUALLY`

## Non-goals

- Генерация вымышленных PDF/инфографик в MVP
- (Day 6) автоотправка — вне scope генерации; **автосабмит включён на Day 7** по решению заказчика (см. product-map), без кнопки оператора

## User-visible behavior

- По APPLY job оператор нажимает «Сгенерировать отклик» или worker создаёт DRAFT.
- Видит полный текст и привязанные case IDs.
- Может отредактировать перед отправкой.
- После отправки статус job → `SENT` или `SENT_MANUALLY`.

## Invariants

- Один активный DRAFT proposal на job (идемпотентность).
- В тексте не утверждать опыт, которого нет в выбранных кейсах.
- Язык отклика = язык описания вакансии (default EN).

## Edge cases and failure policy

- Нет подходящих кейсов → LLM использует общий опыт без выдуманных имён проектов.
- Лимит символов Upwork → сокращение с приоритетом блоков 1–3 и 6.

## Route / state / data implications

- `Proposal`: content, estimate, selected_cases (JSON ids), status `DRAFT|APPROVED|SENT`
- `Job.status`: `PROPOSAL_DRAFTED`, `APPROVED`, `SENT_MANUALLY`

## Verification mapping

- `tests/test_proposals.py`: detect_language, парс JSON (valid/fenced/missing→raise), assemble содержит все блоки, идемпотентность DRAFT, ERROR при плохом LLM, батч.
- Живая генерация для 1 реальной вакансии → все 7 секций присутствуют (подтверждено Day 6).
- Ручной чеклист качества в UI.

## Unknowns

- Нужен ли отдельный prompt template файл vs inline в коде.
