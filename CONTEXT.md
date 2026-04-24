# Контекст проекта и история правок

Этот файл — краткий ввод в курс дела для нового чата с Claude. Описывает что за проект, как он устроен, что уже сделано, где были ловушки.

## Что за проект

**MAX Greeting Bot** — бот в мессенджере МАХ для Мэрии Казани (хакатон).
Пользователь через бот собирает персонализированное поздравление: выбирает повод и стиль → GigaChat генерирует текст + открытку → бот показывает превью → пользователь подтверждает → поздравление отправляется получателю в MAX или на email. Есть история, отложенная отправка, динамические поводы по дате.

**Репозиторий:** https://github.com/katfit-coder/max-greeting-bot
**Деплой:** https://max-greeting-bot.onrender.com (Render free tier, засыпает через 15 минут)
**Текущая версия:** `GET /version` → `2026-04-24-v5-scheduling-regional-holidays`

## Стек

- Python 3.11.9 (прибит в `render.yaml` — иначе Render берёт 3.14, а pydantic-core не собирается из-за Rust/cargo в read-only FS).
- FastAPI + uvicorn.
- SQLAlchemy + SQLite (файл эфемерный, миграции вручную через `ALTER TABLE` в `init_db`).
- httpx для внешних вызовов.
- Без отдельного фронта — только бот-чат в MAX.

## Внешние интеграции

### GigaChat
- OAuth2: `POST https://ngw.devices.sberbank.ru:9443/api/v2/oauth` с `Authorization: Basic <key>` и `scope=GIGACHAT_API_B2B`. **Важно:** scope `_PERS` этому ключу не подходит (ошибка «scope from db not fully includes consumed scope»), используй `_B2B`.
- Chat: `POST https://gigachat.devices.sberbank.ru/api/v1/chat/completions`. Модель — **`GigaChat-2-Max`**. Модели `GigaChat` и `GigaChat-2` на этом ключе возвращают **402 Payment Required**.
- Картинки: тот же endpoint с `function_call=auto`, ответ содержит `<img src="<file_id>" />`, сам файл тянется через `/api/v1/files/<file_id>/content`.
- `verify=False` в httpx — иначе падает на сертификате Минцифры.

### MAX Bot API
- Base URL: `https://botapi.max.ru`
- **Авторизация только через `Authorization` header.** Передача `access_token` в query — **deprecated, возвращает 401**.
- Эндпоинты: `/messages` (POST, chat_id в query), `/uploads`, `/subscriptions` (POST/GET), `/answers` (ответ на callback).
- Типы апдейтов: `message_created`, `message_callback`, `bot_started`, `bot_added`. Подписываемся на все четыре.
- Картинки в сообщениях — через `{type: "image", payload: {url: "..."}}`. Загрузка через `/uploads` слабо документирована и оказалась ненадёжной, поэтому мы хостим картинки сами на `/image/{id}.jpg` и передаём URL.
- **Ловушка в callback-апдейтах:** `message.sender` там — это БОТ (автор кнопок), а реальный пользователь в `callback.user`. Из-за этого у меня пол-дня состояние терялось. См. `_extract_from` в `flow.py`.

### SMTP
- По умолчанию smtp.yandex.ru:465 (SSL). Переменные `SMTP_*` в env. Для демо не настроено — email-канал без ключей выдаёт внятную ошибку.

## Структура кода

```
app/
├── main.py         # FastAPI, webhook, /health, /version, /image/{id}, /admin/*
├── config.py       # pydantic-settings из env
├── models.py       # UserState, HostedImage, SentGreeting, ScheduledGreeting + init_db с ручными миграциями
├── gigachat.py     # GigaChatClient: текст, картинка
├── max_client.py   # MaxClient: send_message, answer_callback, subscribe_webhook
├── prompts.py      # OCCASIONS_CONFIG (с датами), STYLES, TEXT_SYSTEM, KAZAN_CONTEXT, build_text_prompt, build_image_prompt
├── flow.py         # state machine: handle_update, кнопки, обработка callback/текста, _send_final, process_due_scheduled
├── emailer.py      # SMTP отправка с HTML+inline картинкой
```

## State machine

Шаги в `UserState.step`:
- `idle`
- `choose_occasion` → пользователь видит кнопки поводов
- `await_custom_occasion` → пользователь ввёл «Свой повод» и пишет его текстом
- `await_recipient_info` → бот спросил про имениника, пользователь пишет или жмёт «Пропустить»
- `choose_style`
- `preview` → показано превью с текстом+картинкой и кнопками «Перегенерировать/Сменить/Подтвердить»
- `choose_channel`
- `await_schedule_datetime` → для отложенной отправки
- `await_contact` → ввод email или chat_id
- `after_send` → показаны кнопки «Ещё кому-то / Новое / История / Завершить»

## История правок (хронологически)

1. **Скелет** — FastAPI + SQLAlchemy + GigaChat + MaxClient + flow.
2. **GigaChat scope fix** — заменили `GIGACHAT_API_PERS` на `GIGACHAT_API_B2B` (auth 400 «scope not included»).
3. **Модель GigaChat** — заменили `GigaChat` на `GigaChat-2-Max` (402 Payment Required на дефолтной).
4. **Render deploy** — первый билд свалился из-за Python 3.14 и pydantic-core; прибили `PYTHON_VERSION=3.11.9` через envVars.
5. **Диск на free tier** — убрали `disk:` и сменили `DATABASE_URL` на ephemeral `sqlite:///./bot.db`.
6. **MAX auth fix** — убрали `access_token` из query, оставили только `Authorization` header.
7. **bot_started** — подписались на событие; кнопка «Начать» в MAX шлёт именно его, не `/start`.
8. **Картинки через URL** — отказались от MAX /uploads (плохо задокументировано), хостим сами на `/image/{id}.jpg`.
9. **user_id extraction fix (большой)** — в `message_callback` читаем `callback.user.user_id`, а не `message.sender.user_id` (там бот); раньше колбэк и текст попадали в разные state-записи, «не помнил контекст».
10. **Split text+image** — текст отправляется сразу, картинка вторым сообщением (GigaChat картинка иногда до минуты).
11. **Кастомный повод** — кнопка «Свой повод» → ввод текстом.
12. **Персонализация** — шаг «расскажи об имениннике» с «Пропустить».
13. **P.S. с праздником дня** — в промпте просим GigaChat упомянуть другое событие сегодня, если есть.
14. **Resend flow** — после отправки кнопка «Отправить ещё кому-то» без повторной генерации.
15. **Ценности Мэрии Казани** — `KAZAN_CONTEXT` подмешивается в системный промпт при стилях `corporate`/`official`.
16. **Динамические поводы** — `OCCASIONS_CONFIG` с date-range, `current_available_occasions()` фильтрует по сегодняшней дате. Актуальные + всегда-доступные (рабочие: повышение, мотивация и т.п.).
17. **История как UI** — кнопка «📜 История» в меню. Каждая запись — отдельное сообщение с полными деталями и картинкой.
18. **«Завершить на сегодня»** — кнопка после отправки.
19. **Отложенная отправка** — модель `ScheduledGreeting`, кнопка «⏰ Запланировать», парсер дат, `/admin/tick` и автоматическая обработка на каждом webhook-запросе. Команда `/scheduled`.
20. **Регион+рабочие** — 5 татарстанских поводов, 11 российских, 5 рабочих ситуаций (повышение, юбилей, новый коллега, успех проекта, мотивация).

## Важные env-переменные (в Render)

```
PYTHON_VERSION=3.11.9
MAX_BOT_TOKEN=<секрет>          # sync:false
GIGACHAT_AUTH_KEY=<секрет>      # sync:false, Base64
GIGACHAT_SCOPE=GIGACHAT_API_B2B
PUBLIC_BASE_URL=https://max-greeting-bot.onrender.com
DATABASE_URL=sqlite:///./bot.db
SMTP_HOST/PORT/USER/PASSWORD/FROM    # опционально, только для email-канала
```

## Полезные admin-эндпоинты

- `GET /` — статус и что сконфигурировано.
- `GET /version` — билд-метка (удобно убедиться что последний деплой применился).
- `GET /admin/last-updates` — последние 20 апдейтов от MAX (диагностика).
- `GET /admin/subscriptions` — текущая подписка webhook.
- `POST /admin/subscribe` — переподписать webhook на `${PUBLIC_BASE_URL}/webhook`.
- `POST /admin/tick` — обработать отложенные поздравления (для cron-job.org).

## Что явно стоит улучшить (не сделано)

- `HostedImage` и `ScheduledGreeting` хранятся в эфемерной SQLite — при рестарте Render всё обнуляется. Для прод стоит вынести на Persistent Disk (платный план) или внешнюю БД.
- Бот на free-tier засыпает. Отложенная отправка не сработает вовремя, если никто не пинговал бота. Решение — привязать cron-job.org к `/admin/tick`.
- Нет антимисюза: любой желающий, зная chat_id, может триггерить генерацию. Для прод — привязать к whitelist пользователей Мэрии.
- Авторизация MAX-webhook’а у нас никак не проверяется. Теоретически можно подделать апдейт, если знать URL. Для прод — проверка подписи / IP-whitelist MAX.
- Email-канал не протестирован на реальной отправке (SMTP не настроен). Код написан, но нужно реальные креды.

## Где искать ответы на типичные вопросы

- «Как собирается промпт» → `app/prompts.py`, функции `build_text_prompt` / `build_image_prompt`, плюс `TEXT_SYSTEM` и `KAZAN_CONTEXT`.
- «Кто что обрабатывает» → `app/flow.py`, `handle_update` → `_handle_message` / `_handle_callback` / `_handle_bot_started`.
- «Как выглядит апдейт от MAX» → дёрни `GET /admin/last-updates` после действия в боте.
- «Почему картинка пришла как ссылка» → значит MAX не смог скачать `/image/{id}.jpg`; проверь что Render не спит и URL публично открывается в браузере.
