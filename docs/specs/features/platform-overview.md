# Platform Overview

## Goal

Единая точка управления AI-продажами на Upwork: стратегия в задаче, автоматизация вакансий и откликов, переписка и отчётность.

## Scope

- Streamlit как основной UI
- SQLite как хранилище (`data/app.db`)
- OpenRouter для LLM
- Playwright + Chrome для Upwork
- Отдельный worker для фоновых циклов (to-be)

## Non-goals

- Мобильное приложение
- Мульти-тенант SaaS с биллингом
- Официальный Upwork API как единственный канал (ограничен; MVP — браузер)

## User-visible behavior

- Оператор открывает веб-UI и видит разделы: Dashboard, Компании/Задачи, Вакансии, Кейсы, Клиенты, Вопросы агенту, Отчёты (по мере реализации).
- Все действия агента привязаны к **активной задаче** (стратегия из чата задачи влияет на skip/proposal).
- Ошибки LLM/Upwork показываются в UI без падения всего приложения.

## Invariants

- Секреты не хранятся в git; сессия браузера — в `data/`, в `.gitignore`.
- Продуктовая правда по фичам — в `docs/specs/features/`, не только в чате.

## Edge cases and failure policy

- Нет `OPENROUTER_API_KEY` → LLM-функции недоступны, сообщение в UI; ручной CRUD работает.
- Нет сессии Upwork → ingest/submit недоступны, явная подсказка запустить `upwork_connect.py`.

## Route / state / data implications

- Одна БД SQLite на инстанс приложения.
- Файлы: `data/app.db`, `data/upwork_profile/`, `data/upwork_storage_state.json`.

## Verification mapping

- `streamlit run app.py` — UI стартует
- `python upwork_connect.py` — сессия Upwork
- `python ai.py` — OpenRouter

## Unknowns

- Деплой: только local vs server с постоянным Chrome.
