# Proposal Status Sync

Статус: **as-built** (логика); fetch — live-проверка.

## Goal

Держать статусы откликов в приложении в соответствии с реальностью Upwork.
Приложение может разойтись с Upwork (ручной откат, ложное «не подтверждено»,
отклик отправлен иначе) — синхронизация это устраняет.

## Scope

- `proposals_sync.py`: `capture_proposals` (probe), `parse_submitted` (raw→records),
  `sync_submitted` (матч с Job → `SENT`), `fetch_submitted` (browser), `sync_from_upwork`.
- UI: кнопка «🔄 Синхронизировать статусы откликов с Upwork» на вкладке «Вакансии».

## User-visible behavior

- Оператор жмёт sync → читаются «Submitted proposals» с Upwork → совпадающие вакансии помечаются `SENT`.
- Результат: найдено / совпало / помечено SENT / без совпадения.

## Invariants

- Матч по `upwork_job_id` (приоритет), иначе по точному `title`.
- Идемпотентно: уже `SENT` не трогаем.
- Источник истины при расхождении — Upwork.

## Edge cases and failure policy

- Источник данных (NUXT/API) недоступен → `fetch` пуст, ошибка не ломает БД.
- Нет совпадения по job → `unmatched++`, без изменений.

## Route / state / data implications

- Меняет `Job.status`→SENT и связанный `Proposal.status`→SENT (+`submitted_at`).

## Verification mapping

- `tests/test_proposals_sync.py`: parse разных форм, матч по id/по title, идемпотентность, unmatched.
- Live: `sync_from_upwork()` помечает реально отправленный VR Developer как SENT.

## Unknowns

- Точный источник данных страницы `/nx/proposals/` (NUXT vs API) — подтвердить захватом.
