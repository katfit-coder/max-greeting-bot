# MAX Greeting Bot

Бот в мессенджере МАХ для автоматизации корпоративных поздравлений с использованием GigaChat (текст + открытка) и доставкой в MAX-чат или на email.

## Быстрый старт (локально)

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env
# заполнить .env
uvicorn app.main:app --reload
```

## Архитектура (кратко)

```
MAX → webhook POST /webhook → flow.handle_update(update)
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
   GigaChat API              SQLite (state/history)       SMTP / MAX API
   (текст + картинка)                                     (доставка)
```

- `app/main.py` — FastAPI, webhook, lifespan (инициализация БД, подписка на webhook).
- `app/flow.py` — конечный автомат диалога (повод → стиль → превью → канал → отправка). Работает с `message_created` и `message_callback`.
- `app/gigachat.py` — авторизация OAuth2 + `/chat/completions` (текст) + генерация изображения с `function_call=auto` и скачиванием файла по `file_id`.
- `app/max_client.py` — отправка сообщений/кнопок/изображений, управление подписками webhook.
- `app/prompts.py` — поводы, стили, системные промпты и визуальные подсказки для картинок.
- `app/emailer.py` — SMTP-доставка с HTML-версией и инлайн-картинкой (CID).
- `app/models.py` — `UserState` (текущий шаг диалога по пользователю) + `SentGreeting` (история).

## Поводы и стили

**Поводы (6):** День рождения, Новый год, 8 марта, 23 февраля, День программиста, благодарность коллеге.
**Стили (5):** официальный, тёплый, корпоративный, с юмором, дружеский.
Добавить новый повод — одна строка в `OCCASIONS` + описание в `IMAGE_DESCRIPTIONS` (`app/prompts.py`).

## Дополнительные фичи сверх обязательных требований

- **История отправок** — команда `/history` показывает 5 последних.
- **Два канала доставки** — MAX и email (HTML + inline-картинка).
- **Дружественная обработка ошибок** — понятное сообщение пользователю при сбое GigaChat/SMTP/некорректном вводе.
- **Перегенерация отдельно текста и картинки** — не тратим время на повторную генерацию того, что устроило.
- **Возврат на предыдущие шаги** — «Сменить стиль», «Сменить повод» без сброса прогресса.

## Команды бота

- `/start` — начать новое поздравление.
- `/history` — последние 5 отправок.
- `/cancel` — сбросить текущий диалог.

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `MAX_BOT_TOKEN` | Токен бота MAX |
| `GIGACHAT_AUTH_KEY` | Authorization key GigaChat (Base64) |
| `GIGACHAT_SCOPE` | `GIGACHAT_API_PERS` (персональный) или `GIGACHAT_API_CORP` |
| `PUBLIC_BASE_URL` | Публичный HTTPS-адрес сервиса (Render выдаёт) — для регистрации webhook |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM` | Настройки SMTP для email-канала |

## Деплой на Render

1. Создать репозиторий на GitHub, запушить код.
2. В Render: New → Blueprint → выбрать репо → Apply.
3. В Environment указать `MAX_BOT_TOKEN`, `GIGACHAT_AUTH_KEY`, `PUBLIC_BASE_URL` (станет известен после первого деплоя — заполнить и пересохранить).
4. После деплоя Render сам подпишет webhook при старте. Если не сработало — `POST /admin/subscribe`.
