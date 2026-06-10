# Proposal Status Sync

Статус: **as-built** (логика); fetch — live-проверка.

## Goal

Держать статусы откликов в приложении в соответствии с реальностью Upwork.
Приложение может разойтись с Upwork (ручной откат, ложное «не подтверждено»,
отклик отправлен иначе) — синхронизация это устраняет.

## Scope

- `proposals_sync.py`: `capture_proposals` (probe), `parse_submitted` (raw→records),
  `sync_submitted` (матч с Job → `SENT`), `fetch_submitted` (browser), `sync_from_upwork`.
- **Авто-исходы (gap #5)**: `parse_archived` (raw→records со `status_text`),
  `classify_outcome` (текст → WIN/LOST/None), `sync_outcomes` (проставить `Job.outcome`),
  `fetch_archived` (страница `/nx/proposals/archived`), `sync_outcomes_from_upwork`.
- UI: кнопка «🔄 Синхронизировать статусы и исходы откликов» на вкладке «Вакансии».
- Worker: `OUTCOME_SYNC_EVERY=N` — авто-синк исходов раз в N тиков (0 = выкл, дефолт кода 0;
  в `.env` рекомендовано 12). Статус: `last_outcomes_win/lost/reason`.
- Чат с агентом: инструмент `sync_outcomes`.

## User-visible behavior

- Оператор жмёт sync → «Submitted proposals» помечают вакансии `SENT`; архив откликов
  проставляет исходы: **hired → WIN**, **declined → LOST + LLM-разбор** (обучение),
  **job closed → LOST** с фикс-причиной «закрыт без найма» (без LLM — учиться не на чем).
- Результат: SENT-sync (найдено/совпало/помечено) + исходы (WIN/declined/closed).

## Invariants

- Матч по `upwork_job_id` (приоритет), иначе по точному `title`.
- Идемпотентно: уже `SENT` не трогаем; **существующий `Job.outcome` никогда не перезаписывается**.
- Исход ставится только вакансиям в статусе `SENT` (черновики не трогаем).
- «Proposal withdrawn» (мы сами отозвали) и неизвестные статусы → пропуск (None), не LOST.
- LLM post-mortem — только для `declined`; `closed` — без LLM.
- Источник истины при расхождении — Upwork.

## Edge cases and failure policy

- Источник данных (NUXT/API) недоступен → `fetch` пуст, ошибка не ломает БД.
- Нет совпадения по job → `unmatched++`, без изменений.

## Route / state / data implications

- Меняет `Job.status`→SENT и связанный `Proposal.status`→SENT (+`submitted_at`).

## Verification mapping

- `tests/test_proposals_sync.py`: parse разных форм, матч по id/по title, идемпотентность, unmatched;
  `classify_outcome` (hired/declined/closed/withdrawn/empty), `parse_archived` (формы status,
  вложенный label), `sync_outcomes` (WIN/declined+LLM/closed-без-LLM, guards: outcome set,
  не-SENT, unmatched).
- Live: `sync_from_upwork()` помечает реально отправленный VR Developer как SENT.

## Unknowns

- Точный источник данных страницы `/nx/proposals/` (NUXT vs API) — подтвердить захватом.
- Точные формулировки статусов архива (`/nx/proposals/archived`) — парсер толерантный
  (`_STATUS_KEYS` + регэкспы), уточнить по живому прогону `sync_outcomes_from_upwork()`.
