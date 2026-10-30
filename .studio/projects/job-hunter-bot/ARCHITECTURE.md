# Архитектура — job-hunter-bot v1

Версия 1.0 · 2026-10-30 · Основа: SPEC v0.3 · Режим студии: lite

## 1. Стек и решения

| Слой | Выбор | Почему |
| --- | --- | --- |
| Язык | Python 3.12+ | Требование владельца |
| Бот | aiogram 3, long polling | Без домена и TLS для webhook; проще деплой |
| HTTP API | FastAPI (только `/health` и служебное) | REQ-016; в v1.1 сюда же — API для Mini App |
| БД | PostgreSQL 16 (prod), SQLite/aiosqlite (тесты) через SQLAlchemy 2 async | Один код моделей; тесты без контейнера |
| Миграции | `Base.metadata.create_all` в v1, Alembic — v1.1 | Экономия времени, схема пока меняется (ADR-001) |
| HTTP-клиент | httpx (async) | Источники и LLM, легко мокать (`MockTransport`) |
| RSS | feedparser | Устойчивый разбор |
| Шифрование ключей | `cryptography.Fernet` (AES-128-CBC + HMAC), мастер-ключ `MASTER_KEY` в env | Проверенная схема с аутентификацией |
| Планировщик | asyncio-задача в процессе бота | Без лишнего сервиса (Celery/Redis не нужны на этом объёме) |

## 2. Компоненты

```
app/
  config.py          настройки из env (pydantic-settings)
  db.py              engine, session, Base, init_db
  models.py          User, AIKey, Offer, Delivery, LLMUsage
  core/              чистая логика, без БД и сети
    crypto.py        encrypt/decrypt/mask ключа
    urls.py          normalize_url, detect_source
    filtering.py     passes_filters(offer, filters) -> (bool, reason)
    scoring.py       keyword_score(offer, profile, filters)
    llm_parse.py     parse_score_json(text) -> (score, reason) | None
    prompts.py       build_score_messages / build_draft_messages (данные в разделителях)
    cards.py         render_card(offer, score, reason, ai_used) -> HTML (экранировано)
    ratelimit.py     SlidingWindowLimiter
  llm/
    providers.py     пресеты (base_url, модель по умолчанию)
    client.py        OpenAI-совместимый chat completion через httpx
  sources/
    habr.py          RSS Хабр Карьеры -> list[RawOffer]
    hh.py            официальный API hh.ru (только при HH_TOKEN)
    page.py          чтение одной страницы по ссылке пользователя с учётом robots.txt
  services/          бизнес-операции с БД — общий слой для бота, API и будущего MCP
    users.py         регистрация, согласие, профиль, фильтры, ключ, удаление
    offers.py        ingest (дедуп), evaluate (фильтр -> LLM/fallback), mark
    llm_usage.py     суточный лимит
  bot/
    handlers.py      команды и кнопки (тонкие, всё через services)
    keyboards.py
    middlewares.py   rate limit, проверка регистрации
    scheduler.py     цикл сбора: источники -> ingest -> evaluate -> отправка
  api.py             FastAPI: /health, /internal/stats (токен)
  main.py            запуск бота, планировщика и API (uvicorn) в одном процессе
```

Правило зависимостей: `core` ни от чего не зависит; `services` зависят от `core`, `models`, `llm`; `bot` и `api` — только от `services` и `core`. Это и есть задел под MCP-коннектор (этап 2).

## 3. Модель данных

- **User**: id, tg_id (unique), username, consent_at, created_at, profile (JSON: search_types, stack, level, work_format, city, min_salary, about), filters (JSON: keywords, stop_words, min_salary, work_format, city, threshold=60), is_onboarded.
- **AIKey**: id, user_id (unique, FK cascade), provider, model, base_url, key_encrypted, key_last4, created_at.
- **Offer**: id, source, external_id, url_norm (unique), title, company, salary_from, salary_to, currency, city, remote (bool|null), description (до 8000 символов), published_at, fetched_at.
- **Delivery**: id, user_id (FK cascade), offer_id (FK cascade), score, reason, ai_used, status (sent | hidden | favorite | filtered), created_at; unique (user_id, offer_id).
- **LLMUsage**: user_id, day, count; PK (user_id, day).

Удаление пользователя каскадом удаляет AIKey, Delivery, LLMUsage (REQ-014).

## 4. Основные потоки

1. **Сбор (каждые `POLL_MINUTES`)**: собрать запросы всех onboarded-пользователей → для уникальных запросов Хабр RSS (и hh.ru при токене) → `offers.ingest` (дедуп по url_norm) → для каждого пользователя и каждого нового предложения `offers.evaluate` → карточка, если балл ≥ порога.
2. **evaluate**: `filtering` → если не прошёл, Delivery(status=filtered, без вызова LLM) → если есть ключ и лимит не исчерпан: LLM → `llm_parse`; иначе или при ошибке `keyword_score` (ai_used=False).
3. **Ручная ссылка**: `detect_source` → `page.fetch` (robots.txt) → ingest → evaluate без фильтров и порога. Ошибка → просьба прислать текст; текст → Offer(source=manual).
4. **Черновик**: проверка ключа → `build_draft_messages` → LLM → текст пользователю (экранирован).
5. **Подключение ключа**: выбор пресета → сообщение с ключом удаляется → тестовый запрос → шифрование и сохранение.

## 5. Безопасность

- Ключ пользователя расшифровывается только внутри `llm.client` на время запроса; `repr` моделей и логи не содержат ключ; ошибки провайдера пересказываются без тела запроса.
- Промпт: system-инструкция + данные вакансии внутри `<offer>…</offer>`; символы `<`/`>` в данных экранируются, чтобы нельзя было закрыть тег; от LLM принимается только JSON-схема (оценка) или текст (черновик).
- Весь внешний текст в Telegram — через `html.escape`, parse_mode=HTML.
- Rate limit — middleware (REQ-015). Изоляция — все запросы services фильтруются по user_id из апдейта, а не из данных кнопки (в callback_data только offer_id, принадлежность проверяется).
- Порт Postgres не публикуется; API слушает внутри compose, наружу — только `/health` через порт, если нужно.

## 6. Качество

```toml
[quality]
cwd = "../../.."
lint = ["ruff check app tests"]
test = ["python -m pytest -q --cov=app --cov-fail-under=70"]
security = ["bandit -q -r app -ll"]
```

## 7. Задачи

| ID | Что | REQ |
| --- | --- | --- |
| T-001 | Ядро `app/core/*` и пресеты провайдеров | REQ-004, 006, 007 (crypto), 008, 009 (fallback, parse), 010, 012, 015 (limiter) |
| T-002 | Модели и `services/*` | REQ-001, 002, 003, 006, 007, 009, 011, 014 |
| T-003 | Источники `sources/*` и `llm/client.py` | REQ-005, 007 (проверка ключа), 013, 018 |
| T-004 | Бот, планировщик, API | REQ-001, 002, 003, 007, 010, 011, 013, 015, 016 |
| T-005 | Docker, compose, CI, README | REQ-017 |

## ADR-001: без Alembic в v1

`create_all` при старте. Риск: изменение схемы в v1.1 потребует миграции — Alembic подключим одновременно с первой несовместимой правкой. Принято.
