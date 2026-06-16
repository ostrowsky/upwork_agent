# Client Conversations

Статус: **as-built (Day 8)**.

## Goal

Каждый ответ заказчика → карточка клиента; AI ведёт переписку, оператор может отключить AI и писать сам.

## Реализация (Day 8)

- Модуль `clients.py`:
  - `get_or_create_client(name, job_id)` — карточка на контакт (идемпотентно).
  - `record_inbound(content, name, job_id)` — добавляет inbound, при `ai_enabled` генерит черновик в `ClientMessage.ai_draft`.
  - `build_reply_messages` — история: inbound→user, outbound→assistant; `generate_draft_text` — LLM.
  - `send_reply` — outbound (append-only); `set_ai_enabled` — тогл AI.
- UI «Клиенты»: импорт inbound, лента сообщений, AI-черновик с правкой, кнопка отправки (запись outbound), тогл AI, перегенерация черновика.
- MVP: inbound импортируется вручную (полный sync инбокса Upwork без браузера — non-goal).

## Scope

- `Client`, `ClientMessage` (схема as-built)
- Создание клиента при первом inbound сообщении (ручной импорт или sync Upwork to-be)
- `ai_enabled` toggle в UI
- LLM черновик в `ai_draft` для outbound

## Non-goals

- Полная двусторонняя синхронизация Upwork messages без браузера в MVP

## User-visible behavior

- Новое сообщение клиента → новая/обновлённая карточка в разделе «Клиенты».
- При `ai_enabled=1` показывается черновик ответа; оператор Send или Edit.
- При `ai_enabled=0` только ручной ввод.

## Invariants

- Сообщения append-only (не перезаписывать history).
- Клиент связан с `job_id`, если известен контекст вакансии.

## Edge cases and failure policy

- Несколько jobs у одного клиента → одна карточка client, несколько job links (TBD).

## Route / state / data implications

- `ClientMessage.direction`: `inbound` | `outbound`
- `Client.ai_enabled`: 1/0

## Verification mapping

- `tests/test_clients.py`: inbound создаёт клиента+черновик; нет черновика при AI off; роли inbound/outbound; append-only история; идемпотентность карточки.

## Unknowns

- Polling inbox vs webhook (нет у Upwork для нас).
