# Daily Reporting

Статус: **as-built (Day 10)**.

## Goal

Ежедневная сводка в Telegram/Discord и история в UI.

## Реализация (Day 10)

- БД: таблица `daily_reports` (day, task_id, metrics_json, text).
- `reporting.py`: `build_report` (воронка + revenue/connect + best/underperforming bucket), `format_report_text` (golden-шаблон ТЗ), `save_report` (один на день/задачу — upsert), `send_telegram`/`send_discord` (HTTP), `send_report`, `send_alert`.
- `telegram_report.py`: CLI для cron/ручного запуска.
- Worker: раз в день шлёт отчёт; при НОВЫХ аномалиях шлёт алерт (`send_alert`) — закрывает техдолг #3.
- UI Dashboard: кнопка «Отправить отчёт сейчас» + история отчётов.
- Каналы недоступны/не настроены → отчёт сохраняется в БД, ошибка не теряет данные.

## Scope

- Метрики: connects spent, replies, interviews, hires, revenue, revenue/connect, best/worst niche, best budget range
- `DailyReport` snapshot в БД
- `telegram_report.py` отправка
- UI: история отчётов

## Non-goals

- BI-дашборды вне Streamlit

## User-visible behavior

- В конце дня (cron или кнопка) оператор видит отчёт в Telegram и в UI.
- Часть полей может вводиться вручную, если автосбор недоступен.

## Invariants

- Один отчёт на календарный день per company (или per task — TBD).
- Токены Telegram только в .env.

## Edge cases and failure policy

- Telegram недоступен → отчёт только в UI + log error.

## Route / state / data implications

- Таблица `daily_reports` (JSON metrics + date + company_id)

## Verification mapping

- `tests/test_reporting.py`: build/format (Spent connects/Hires/Revenue), один отчёт на день (upsert), send с мок-каналами пишет в БД, telegram not-configured.

## Unknowns

- Автоматический подсчёт connects из Upwork UI vs ручной ввод.
