import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.emailer import EmailError, send_greeting_email
from app.gigachat import GigaChatClient
from app.max_client import MaxClient
from app.models import HostedImage, ScheduledGreeting, SentGreeting, UserState
from app.prompts import (
    OCCASION_LABELS,
    STYLE_LABELS,
    STYLES,
    TEXT_SYSTEM,
    build_image_prompt,
    build_text_prompt,
    current_available_occasions,
)

log = logging.getLogger("flow")

EMAIL_RE = re.compile(r"^[\w.\-+]+@[\w\-]+\.[\w.\-]+$")


def _occasion_buttons() -> list[list[dict]]:
    rows = []
    current: list[dict] = []
    for key, label in current_available_occasions():
        current.append({"type": "callback", "text": label, "payload": f"occasion:{key}"})
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows.append([{"type": "callback", "text": "✏️ Свой повод", "payload": "occasion:custom"}])
    rows.append([{"type": "callback", "text": "📜 История поздравлений", "payload": "history"}])
    return rows


def _recipient_info_buttons() -> list[list[dict]]:
    return [[{"type": "callback", "text": "➡️ Пропустить", "payload": "skip_info"}]]


def _after_send_buttons() -> list[list[dict]]:
    return [
        [
            {"type": "callback", "text": "📤 Отправить ещё кому-то", "payload": "resend"},
            {"type": "callback", "text": "🆕 Новое поздравление", "payload": "restart"},
        ],
        [
            {"type": "callback", "text": "📜 История", "payload": "history"},
            {"type": "callback", "text": "🏁 Завершить на сегодня", "payload": "finish"},
        ],
    ]


def _quick_actions() -> list[list[dict]]:
    """Универсальные действия — для использования в любых текстовых ответах,
    где раньше упоминались /start /cancel /history."""
    return [
        [
            {"type": "callback", "text": "🆕 Начать заново", "payload": "restart"},
            {"type": "callback", "text": "📜 История", "payload": "history"},
        ],
        [{"type": "callback", "text": "◀️ Отмена", "payload": "cancel"}],
    ]


def _style_buttons() -> list[list[dict]]:
    rows = []
    current: list[dict] = []
    for key, label in STYLES:
        current.append({"type": "callback", "text": label, "payload": f"style:{key}"})
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    return rows


def _preview_buttons() -> list[list[dict]]:
    return [
        [
            {"type": "callback", "text": "🔄 Перегенерировать текст", "payload": "regen:text"},
            {"type": "callback", "text": "🎨 Новая картинка", "payload": "regen:image"},
        ],
        [
            {"type": "callback", "text": "🎭 Сменить стиль", "payload": "back:style"},
            {"type": "callback", "text": "📋 Сменить повод", "payload": "back:occasion"},
        ],
        [{"type": "callback", "text": "✅ Подтвердить", "payload": "confirm"}],
    ]


def _channel_buttons_no_schedule() -> list[list[dict]]:
    return [
        [
            {"type": "callback", "text": "✉️ На email", "payload": "channel:email"},
            {"type": "callback", "text": "👥 Кому-то из бота", "payload": "channel:bot_user"},
        ],
        [{"type": "callback", "text": "📱 Себе в MAX (тест)", "payload": "channel:max_self"}],
        [{"type": "callback", "text": "◀️ Отмена", "payload": "cancel"}],
    ]


def _channel_buttons() -> list[list[dict]]:
    return [
        [
            {"type": "callback", "text": "✉️ На email", "payload": "channel:email"},
            {"type": "callback", "text": "👥 Кому-то из бота", "payload": "channel:bot_user"},
        ],
        [
            {"type": "callback", "text": "📱 Себе в MAX (тест)", "payload": "channel:max_self"},
            {"type": "callback", "text": "⏰ Запланировать", "payload": "schedule"},
        ],
        [
            {"type": "callback", "text": "📦 Сохранить в историю", "payload": "save_only"},
            {"type": "callback", "text": "◀️ Отмена", "payload": "cancel"},
        ],
    ]


def _bot_user_directory_buttons(db: Session, exclude_user_id: int) -> list[list[dict]]:
    """Возвращает кнопки со списком пользователей бота (кроме самого отправителя).
    Каждая кнопка → callback pick:<user_id>."""
    rows: list[list[dict]] = []
    users = (
        db.query(UserState)
        .filter(UserState.user_id != exclude_user_id)
        .order_by(UserState.updated_at.desc())
        .limit(20)
        .all()
    )
    cur: list[dict] = []
    for u in users:
        label = u.display_name or f"id {u.user_id}"
        cur.append({"type": "callback", "text": f"👤 {label[:24]}", "payload": f"pick:{u.user_id}"})
        if len(cur) == 2:
            rows.append(cur)
            cur = []
    if cur:
        rows.append(cur)
    rows.append([{"type": "callback", "text": "◀️ Назад", "payload": "back:channel"}])
    return rows


def _extract_display_name(update: dict) -> str:
    """Достаём имя пользователя из любого апдейта."""
    msg = update.get("message") or {}
    callback = update.get("callback") or {}
    candidates = [
        callback.get("user") or {},
        msg.get("sender") or {},
        update.get("user") or {},
    ]
    for c in candidates:
        # пропускаем боты
        if c.get("is_bot"):
            continue
        name = c.get("name") or c.get("first_name") or ""
        last = c.get("last_name") or ""
        if name:
            return (name + (" " + last if last else "")).strip()[:80]
    return ""


def _get_or_create_state(db: Session, user_id: int, chat_id: int, display_name: str = "") -> UserState:
    st = db.query(UserState).filter(UserState.user_id == user_id).first()
    if not st:
        st = UserState(user_id=user_id, chat_id=chat_id, step="idle", display_name=display_name)
        db.add(st)
        db.commit()
        db.refresh(st)
    else:
        changed = False
        if st.chat_id != chat_id:
            st.chat_id = chat_id
            changed = True
        if display_name and st.display_name != display_name:
            st.display_name = display_name
            changed = True
        if changed:
            db.commit()
    return st


def handle_update(
    update: dict, db: Session, max_client: MaxClient, giga: Optional[GigaChatClient]
) -> None:
    u_type = update.get("update_type") or update.get("type")
    if u_type == "message_created":
        _handle_message(update, db, max_client, giga)
    elif u_type == "message_callback":
        _handle_callback(update, db, max_client, giga)
    elif u_type in ("bot_started", "bot_added"):
        _handle_bot_started(update, db, max_client)
    else:
        log.info("unhandled update type: %s", u_type)


def _handle_bot_started(update: dict, db: Session, max_client: MaxClient) -> None:
    chat_id = update.get("chat_id") or (update.get("payload") or {}).get("chat_id")
    user = update.get("user") or (update.get("payload") or {}).get("user") or {}
    user_id = user.get("user_id") or user.get("id") or update.get("user_id")
    if not chat_id or not user_id:
        log.warning("bot_started without chat_id/user_id: %s", update)
        return
    # детектим первый запуск: state ещё не создан
    existing = db.query(UserState).filter(UserState.user_id == user_id).first()
    is_first_time = existing is None
    st = _get_or_create_state(db, user_id, chat_id, display_name=_extract_display_name(update))
    st.step = "choose_occasion"
    db.commit()

    if is_first_time:
        max_client.send_message(
            chat_id,
            "👋 Привет! Я бот-помощник в создании поздравлений по любому поводу.\n\n"
            "Я умею:\n"
            "• подбирать тёплые слова под нужный стиль и повод\n"
            "• рисовать индивидуальную открытку под получателя\n"
            "• отправлять поздравление в MAX, на email или планировать на дату\n"
            "• хранить историю всех твоих отправок\n\n"
            "📌 Несколько простых правил, чтобы я не путался:\n"
            "1. После нажатия кнопки **подожди ответ** — генерация может занимать до минуты.\n"
            "2. Не нажимай несколько кнопок подряд, особенно из старых сообщений.\n"
            "3. Если что-то пошло не так — нажми «Отмена» или жми кнопку «Начать заново».\n\n"
            "Поехали! Выбери повод:",
            buttons=_occasion_buttons(),
        )
    else:
        max_client.send_message(
            chat_id,
            "👋 С возвращением! Выбери повод для нового поздравления:",
            buttons=_occasion_buttons(),
        )


def _extract_from(obj: dict) -> tuple[Optional[int], Optional[int]]:
    """Robustly pull chat_id and user_id from any MAX update shape.

    Important: in message_callback, `message.sender` is the BOT (because the
    message with buttons was sent by the bot), so we MUST prefer callback.user
    as the real user. For message_created, `message.sender` IS the real user.
    """
    u_type = obj.get("update_type") or obj.get("type") or ""
    msg = obj.get("message") or {}
    recipient = msg.get("recipient") or {}
    msg_sender = msg.get("sender") or {}
    callback = obj.get("callback") or {}
    cb_user = callback.get("user") or {}
    top_user = obj.get("user") or {}

    chat_id = (
        recipient.get("chat_id")
        or obj.get("chat_id")
        or callback.get("chat_id")
    )

    if u_type == "message_callback":
        user_id = (
            cb_user.get("user_id") or cb_user.get("id")
            or recipient.get("user_id")
        )
    else:
        user_id = (
            msg_sender.get("user_id") or msg_sender.get("id")
            or top_user.get("user_id") or top_user.get("id")
            or cb_user.get("user_id") or cb_user.get("id")
            or obj.get("user_id")
        )
    return chat_id, user_id


def _extract_shared_contact(msg: dict) -> Optional[dict]:
    """Если в сообщении есть прикреплённый контакт — извлечь его данные.
    Возвращает {user_id, name, phone, has_bot} или None."""
    attachments = (msg.get("body") or {}).get("attachments") or []
    for att in attachments:
        if att.get("type") == "contact":
            payload = att.get("payload") or {}
            return {
                "user_id": payload.get("user_id") or payload.get("contact_id"),
                "name": payload.get("name") or payload.get("first_name") or "",
                "phone": payload.get("phone") or "",
                "has_bot": payload.get("has_bot", False),
            }
    return None


def _handle_message(
    update: dict, db: Session, max_client: MaxClient, giga: Optional[GigaChatClient]
) -> None:
    msg = update.get("message") or {}
    body = msg.get("body") or {}
    text = (body.get("text") or "").strip()
    chat_id, user_id = _extract_from(update)
    if not chat_id or not user_id:
        log.warning("can't resolve chat_id/user_id from update: %s", update)
        return
    st = _get_or_create_state(db, user_id, chat_id, display_name=_extract_display_name(update))

    if text.lower().startswith("/start"):
        st.step = "choose_occasion"
        st.occasion = ""
        st.style = ""
        st.extra_wish = ""
        st.generated_text = ""
        st.generated_image = None
        db.commit()
        max_client.send_message(
            chat_id,
            "👋 Привет! Я помогу собрать красивое поздравление за пару кликов.\n\n"
            "Выбери повод:",
            buttons=_occasion_buttons(),
        )
        return

    if text.lower().startswith("/history"):
        _show_history(st, db, max_client)
        return

    if text.lower().startswith("/scheduled"):
        _show_scheduled(st, db, max_client)
        return

    if text.lower().startswith("/cancel"):
        st.step = "idle"
        db.commit()
        max_client.send_message(
            chat_id,
            "Ок, отменил. Чем заняться дальше?",
            buttons=_quick_actions(),
        )
        return

    if st.step == "await_custom_occasion":
        st.occasion = "custom"
        st.custom_occasion = text[:150]
        st.step = "await_recipient_info"
        db.commit()
        max_client.send_message(
            chat_id,
            f"Повод: «{st.custom_occasion}».\n\nРасскажи пару слов о получателе (имя, что любит, общий контекст) — поздравление получится персональнее. Или пропусти:",
            buttons=_recipient_info_buttons(),
        )
        return

    if st.step == "await_recipient_info":
        st.recipient_info = text[:400]
        st.step = "choose_style"
        db.commit()
        max_client.send_message(chat_id, "Принято. Теперь выбери стиль:", buttons=_style_buttons())
        return

    if st.step == "await_extra_wish":
        st.extra_wish = text[:300]
        db.commit()
        _generate_and_preview(st, db, max_client, giga)
        return

    if st.step == "await_schedule_datetime":
        parsed = _parse_datetime(text)
        if parsed is None:
            max_client.send_message(
                chat_id,
                "❌ Не распознал дату. Примеры: `30.04`, `30.04 14:30`, `30.04.2027 09:00`.",
            )
            return
        if parsed <= datetime.now():
            max_client.send_message(chat_id, "❌ Дата должна быть в будущем. Введи ещё раз.")
            return
        st.scheduled_at = parsed
        st.step = "choose_channel"
        db.commit()
        max_client.send_message(
            chat_id,
            f"⏰ Запланировано на {parsed.strftime('%d.%m.%Y %H:%M')}.\n\nЧерез какой канал отправить в этот момент?",
            buttons=_channel_buttons_no_schedule(),
        )
        return

    if st.step == "await_contact":
        # MAX: получили прикреплённый контакт через нативный picker
        contact = _extract_shared_contact(msg)
        if contact and st.channel in ("max", "max_contact"):
            target = contact.get("user_id")
            name = contact.get("name") or "получатель"
            if not target:
                max_client.send_message(
                    chat_id,
                    f"❌ Контакт {name} не привязан к MAX-аккаунту (есть только телефон). "
                    "Пересылка боту через MAX невозможна — выбери другого получателя или email.",
                )
                return
            _send_final(st, str(target), db, max_client, recipient_label=name)
            return

        if st.channel == "email":
            if not EMAIL_RE.match(text):
                max_client.send_message(chat_id, "❌ Похоже, это не email. Введи адрес вида name@domain.ru")
                return
            _send_final(st, text, db, max_client)
        elif st.channel == "max":
            try:
                target_chat_id = int(text)
            except ValueError:
                max_client.send_message(chat_id, "❌ Для MAX укажи числовой chat_id получателя. Для демо можно отправить самому себе — твой chat_id: " + str(chat_id))
                return
            _send_final(st, str(target_chat_id), db, max_client)
        elif st.channel == "max_contact":
            # пользователь начал писать текст вместо использования кнопки
            max_client.send_message(
                chat_id,
                "Используй кнопку «📲 Выбрать получателя» из предыдущего сообщения, чтобы открыть список контактов MAX.",
            )
        return

    if text.lower().startswith("/"):
        max_client.send_message(
            chat_id,
            "Доступные действия:",
            buttons=_quick_actions(),
        )
        return

    # любой другой текст в idle → показываем главное меню
    st.step = "choose_occasion"
    db.commit()
    max_client.send_message(
        chat_id,
        "Давай соберём поздравление. Выбери повод:",
        buttons=_occasion_buttons(),
    )


def _handle_callback(
    update: dict, db: Session, max_client: MaxClient, giga: Optional[GigaChatClient]
) -> None:
    cb = update.get("callback") or {}
    payload = cb.get("payload") or ""
    callback_id = cb.get("callback_id") or ""
    chat_id, user_id = _extract_from(update)
    if not chat_id or not user_id:
        return
    st = _get_or_create_state(db, user_id, chat_id, display_name=_extract_display_name(update))
    max_client.answer_callback(callback_id)

    # Защита от кликов по старым кнопкам: callback должен соответствовать текущему шагу.
    # Эти callbacks разрешены в любом состоянии (они не ломают поток):
    universal = {"cancel", "history", "finish", "restart", "save_only"}
    # Для state-specific — проверяем ожидаемый шаг:
    expected_step = {
        "skip_info": {"await_recipient_info"},
        "confirm": {"preview"},
        "schedule": {"choose_channel"},
        "resend": {"after_send"},
    }
    if payload not in universal:
        prefix = payload.split(":", 1)[0] if ":" in payload else payload
        step_map = {
            "occasion": {"choose_occasion"},
            "style": {"choose_style"},
            "regen": {"preview"},
            "back": {"preview", "choose_channel"},
            "channel": {"choose_channel"},
            "pick": {"choose_channel"},
        }
        allowed = expected_step.get(payload) or step_map.get(prefix)
        if allowed is not None and st.step not in allowed:
            log.info("ignoring stale callback payload=%s current_step=%s expected=%s",
                     payload, st.step, allowed)
            max_client.send_message(
                chat_id,
                "⚠️ Эта кнопка из старого сообщения — она уже не актуальна.\n"
                "Используй кнопки ниже:",
                buttons=_quick_actions(),
            )
            return

    if payload.startswith("occasion:"):
        key = payload.split(":", 1)[1]
        if key == "custom":
            st.occasion = "custom"
            st.custom_occasion = ""
            st.step = "await_custom_occasion"
            db.commit()
            max_client.send_message(
                chat_id,
                "✏️ Напиши повод своими словами (например: «юбилей 50 лет», «получение диплома», «защита проекта»).",
            )
            return
        st.occasion = key
        st.custom_occasion = ""
        st.step = "await_recipient_info"
        db.commit()
        max_client.send_message(
            chat_id,
            f"Повод: {OCCASION_LABELS.get(st.occasion, st.occasion)}.\n\nРасскажи пару слов о получателе (имя, что любит, общий контекст) — поздравление получится персональнее. Или пропусти:",
            buttons=_recipient_info_buttons(),
        )
        return

    if payload == "skip_info":
        st.recipient_info = ""
        st.step = "choose_style"
        db.commit()
        max_client.send_message(chat_id, "Ок, без доп. контекста. Выбери стиль:", buttons=_style_buttons())
        return

    if payload == "resend":
        st.step = "choose_channel"
        db.commit()
        max_client.send_message(chat_id, "Тот же текст и открытка — куда отправить теперь?", buttons=_channel_buttons())
        return

    if payload == "restart":
        st.step = "choose_occasion"
        st.occasion = ""
        st.custom_occasion = ""
        st.style = ""
        st.extra_wish = ""
        st.recipient_info = ""
        st.generated_text = ""
        st.generated_image = None
        db.commit()
        max_client.send_message(chat_id, "🆕 Новое поздравление. Выбери повод:", buttons=_occasion_buttons())
        return

    if payload.startswith("style:"):
        st.style = payload.split(":", 1)[1]
        db.commit()
        max_client.send_message(chat_id, "✍️ Собираю поздравление, это займёт 5–15 секунд...")
        _generate_and_preview(st, db, max_client, giga)
        return

    if payload == "regen:text":
        max_client.send_message(chat_id, "🔄 Перегенерирую текст...")
        _regen_text(st, db, max_client, giga)
        return

    if payload == "regen:image":
        max_client.send_message(chat_id, "🎨 Перегенерирую открытку...")
        _regen_image(st, db, max_client, giga)
        return

    if payload == "back:style":
        st.step = "choose_style"
        db.commit()
        max_client.send_message(chat_id, "Выбери другой стиль:", buttons=_style_buttons())
        return

    if payload == "back:occasion":
        st.step = "choose_occasion"
        db.commit()
        max_client.send_message(chat_id, "Выбери другой повод:", buttons=_occasion_buttons())
        return

    if payload == "confirm":
        st.step = "choose_channel"
        db.commit()
        max_client.send_message(chat_id, "Куда отправить поздравление?", buttons=_channel_buttons())
        return

    if payload.startswith("channel:"):
        st.channel = payload.split(":", 1)[1]
        db.commit()
        if st.channel == "email":
            st.step = "await_contact"
            db.commit()
            max_client.send_message(chat_id, "📧 Введи email получателя:")
        elif st.channel == "max_self":
            # отправка самому себе (для теста / себе как напоминание)
            _send_final(st, str(chat_id), db, max_client, recipient_label="себе")
        elif st.channel == "bot_user":
            # показываем список пользователей нашего бота
            buttons = _bot_user_directory_buttons(db, exclude_user_id=st.user_id)
            if len(buttons) == 1:  # только кнопка «Назад» — никого больше нет
                max_client.send_message(
                    chat_id,
                    "📭 Пока в боте нет других пользователей кроме тебя.\n"
                    "Когда коллеги запустят бота, они появятся здесь автоматически.",
                    buttons=[[{"type": "callback", "text": "◀️ Назад к каналам", "payload": "back:channel"}]],
                )
            else:
                max_client.send_message(
                    chat_id,
                    "👥 Выбери получателя из тех, кто пользуется ботом:",
                    buttons=buttons,
                )
        elif st.channel == "max":
            st.step = "await_contact"
            db.commit()
            max_client.send_message(
                chat_id,
                f"💬 Введи chat_id получателя в MAX (число).\n\nДля теста можешь отправить на свой — твой chat_id: {chat_id}",
            )
        return

    if payload.startswith("pick:"):
        target_id = payload.split(":", 1)[1]
        target_state = db.query(UserState).filter(UserState.user_id == int(target_id)).first()
        target_chat = target_state.chat_id if target_state else int(target_id)
        target_name = target_state.display_name if target_state and target_state.display_name else f"id {target_id}"
        st.channel = "bot_user"
        db.commit()
        _send_final(st, str(target_chat), db, max_client, recipient_label=target_name)
        return

    if payload == "back:channel":
        st.step = "choose_channel"
        db.commit()
        max_client.send_message(chat_id, "Куда отправить поздравление?", buttons=_channel_buttons())
        return

    if payload == "cancel":
        st.step = "idle"
        st.schedule_mode = 0
        db.commit()
        max_client.send_message(chat_id, "Отменено.", buttons=_quick_actions())
        return

    if payload == "save_only":
        # пользователь не хочет никому отправлять — просто сохраняем в историю
        if not st.generated_text:
            max_client.send_message(chat_id, "⚠️ Нечего сохранять — поздравление ещё не сгенерировано.", buttons=_quick_actions())
            return
        img_id = None
        if st.generated_image:
            hosted = HostedImage(content=st.generated_image)
            db.add(hosted)
            db.flush()
            img_id = hosted.id
        db.add(SentGreeting(
            user_id=st.user_id,
            sender_user_id=st.user_id,
            occasion=st.occasion,
            custom_occasion=st.custom_occasion or "",
            style=st.style,
            channel="saved",
            recipient_contact="—",
            recipient_info=st.recipient_info or "",
            extra_wish=st.extra_wish or "",
            text=st.generated_text,
            has_image=1 if st.generated_image else 0,
            image_id=img_id,
        ))
        st.step = "after_send"
        db.commit()
        max_client.send_message(
            chat_id,
            "📦 Сохранил в историю — отправлять никому не буду.\n\nЧто дальше?",
            buttons=_after_send_buttons(),
        )
        return

    if payload == "schedule":
        st.schedule_mode = 1
        st.step = "await_schedule_datetime"
        db.commit()
        max_client.send_message(
            chat_id,
            "⏰ На какую дату запланировать отправку?\n\n"
            "Форматы:\n"
            "• `30.04` — на 30 апреля в 09:00\n"
            "• `30.04 14:30` — на 30 апреля в 14:30\n"
            "• `30.04.2027 09:00` — с явным годом\n\n"
            "Дата должна быть в будущем.",
        )
        return

    if payload == "history":
        _show_history(st, db, max_client)
        return

    if payload == "finish":
        st.step = "idle"
        db.commit()
        max_client.send_message(
            chat_id,
            "🏁 Готово на сегодня! Отличной работы.\n\nКогда захочешь — используй кнопки ниже.",
            buttons=_quick_actions(),
        )
        return


def _generate_and_preview(
    st: UserState, db: Session, max_client: MaxClient, giga: Optional[GigaChatClient]
) -> None:
    if giga is None:
        max_client.send_message(
            st.chat_id,
            "⚠️ GigaChat не настроен: в окружении нет GIGACHAT_AUTH_KEY. "
            "Добавь ключ в .env (или в Render → Environment) и перезапусти сервис.",
        )
        return
    # 1) текст — быстро
    try:
        text = giga.generate_text(
            TEXT_SYSTEM,
            build_text_prompt(
                st.occasion, st.style, st.extra_wish,
                st.recipient_name, st.sender_name,
                recipient_info=st.recipient_info or "",
                custom_occasion=st.custom_occasion or "",
            ),
        )
    except Exception as e:
        log.exception("text gen failed")
        max_client.send_message(
            st.chat_id,
            f"⚠️ GigaChat не смог сгенерировать текст: {_short(e)}. Попробуй ещё раз через минуту или выбери другой стиль.",
        )
        return

    st.generated_text = text
    st.step = "preview"
    db.commit()

    occ = st.custom_occasion or OCCASION_LABELS.get(st.occasion, st.occasion)
    style = STYLE_LABELS.get(st.style, st.style)
    max_client.send_message(
        st.chat_id,
        f"📝 Черновик поздравления\n\nПовод: {occ}\nСтиль: {style}\n\n{text}",
    )
    max_client.send_message(st.chat_id, "🎨 Теперь рисую открытку (до минуты)...")

    # 2) картинка — медленно, отдельным сообщением
    # 2a) сначала просим арт-директора (тот же GigaChat, но в текстовом режиме) придумать сцену
    scene = ""
    try:
        scene = giga.compose_image_scene(
            occasion_label=st.custom_occasion or OCCASION_LABELS.get(st.occasion, st.occasion),
            style_label=STYLE_LABELS.get(st.style, st.style),
            recipient_info=st.recipient_info or "",
            extra_wish=st.extra_wish or "",
            custom_occasion=st.custom_occasion or "",
        )
        log.info("scene: %s", scene[:200])
    except Exception as e:
        log.warning("scene compose failed: %s — fallback to keyword prompt", e)

    image_url: Optional[str] = None
    try:
        img = giga.generate_image(build_image_prompt(
            occasion_key=st.occasion,
            style_key=st.style,
            recipient_name=st.recipient_name or "",
            recipient_info=st.recipient_info or "",
            extra_wish=st.extra_wish or "",
            custom_occasion=st.custom_occasion or "",
            scene=scene,
        ))
        if img is not None:
            st.generated_image = img.binary
            db.commit()
            image_url = _host_image(db, img.binary)
    except Exception as e:
        log.warning("image gen failed: %s", e)
        max_client.send_message(
            st.chat_id,
            f"⚠️ Открытку не удалось сгенерировать: {_short(e)}. Текст уже готов — можно подтвердить без картинки или перегенерировать.",
            buttons=_preview_buttons(),
        )
        return

    if image_url is None:
        max_client.send_message(
            st.chat_id,
            "⚠️ GigaChat не вернул картинку. Текст готов — подтверди или попробуй перегенерировать.",
            buttons=_preview_buttons(),
        )
        return

    max_client.send_message(
        st.chat_id,
        "✅ Открытка готова:",
        buttons=_preview_buttons(),
        image_url=image_url,
    )


def _regen_text(
    st: UserState, db: Session, max_client: MaxClient, giga: Optional[GigaChatClient]
) -> None:
    if giga is None:
        max_client.send_message(st.chat_id, "⚠️ GigaChat не настроен (нет GIGACHAT_AUTH_KEY).")
        return
    try:
        text = giga.generate_text(
            TEXT_SYSTEM,
            build_text_prompt(
                st.occasion, st.style, st.extra_wish,
                st.recipient_name, st.sender_name,
                recipient_info=st.recipient_info or "",
                custom_occasion=st.custom_occasion or "",
            ),
        )
    except Exception as e:
        max_client.send_message(st.chat_id, f"⚠️ Не получилось: {_short(e)}")
        return
    st.generated_text = text
    db.commit()
    image_url = _latest_image_url(db, st.generated_image)
    caption = f"📝 Новый вариант:\n\n{text}"
    max_client.send_message(st.chat_id, caption, buttons=_preview_buttons(), image_url=image_url)


def _regen_image(
    st: UserState, db: Session, max_client: MaxClient, giga: Optional[GigaChatClient]
) -> None:
    if giga is None:
        max_client.send_message(st.chat_id, "⚠️ GigaChat не настроен (нет GIGACHAT_AUTH_KEY).")
        return
    scene = ""
    try:
        scene = giga.compose_image_scene(
            occasion_label=st.custom_occasion or OCCASION_LABELS.get(st.occasion, st.occasion),
            style_label=STYLE_LABELS.get(st.style, st.style),
            recipient_info=st.recipient_info or "",
            extra_wish=st.extra_wish or "",
            custom_occasion=st.custom_occasion or "",
        )
    except Exception as e:
        log.warning("scene compose failed during regen: %s", e)

    try:
        img = giga.generate_image(build_image_prompt(
            occasion_key=st.occasion,
            style_key=st.style,
            recipient_name=st.recipient_name or "",
            recipient_info=st.recipient_info or "",
            extra_wish=st.extra_wish or "",
            custom_occasion=st.custom_occasion or "",
            scene=scene,
            regen_counter=int(datetime.now().timestamp()) % 10000,
        ))
    except Exception as e:
        max_client.send_message(st.chat_id, f"⚠️ Картинку не получилось: {_short(e)}")
        return
    if img is None:
        max_client.send_message(st.chat_id, "⚠️ GigaChat не вернул изображение. Попробуй ещё раз.")
        return
    st.generated_image = img.binary
    db.commit()
    image_url = _host_image(db, img.binary)
    max_client.send_message(
        st.chat_id,
        f"🎨 Новая открытка к тому же тексту:\n\n{st.generated_text}",
        buttons=_preview_buttons(),
        image_url=image_url,
    )


def _send_final(st: UserState, contact: str, db: Session, max_client: MaxClient,
                recipient_label: str = "") -> None:
    # Если пользователь выбрал отложенную отправку — сохраняем, не шлём сейчас
    if st.schedule_mode and st.scheduled_at:
        img_id = None
        if st.generated_image:
            hosted = HostedImage(content=st.generated_image)
            db.add(hosted)
            db.flush()
            img_id = hosted.id
        db.add(ScheduledGreeting(
            user_id=st.user_id,
            chat_id=st.chat_id,
            scheduled_at=st.scheduled_at,
            channel=st.channel,
            recipient_contact=contact,
            text=st.generated_text,
            image_id=img_id,
            occasion=st.occasion or "",
            custom_occasion=st.custom_occasion or "",
            style=st.style or "",
            recipient_info=st.recipient_info or "",
        ))
        st.step = "after_send"
        st.schedule_mode = 0
        db.commit()
        when = st.scheduled_at.strftime("%d.%m.%Y %H:%M")
        max_client.send_message(
            st.chat_id,
            f"✅ Поздравление запланировано на {when} → отправка в {('MAX' if st.channel=='max' else 'email')}: {contact}.\n\n"
            "Оно уйдёт автоматически. Список запланированных можно посмотреть кнопкой «Запланированные».\n\n"
            "Хочешь ещё что-то собрать или закончить?",
            buttons=_after_send_buttons(),
        )
        return

    try:
        if st.channel == "email":
            subject = f"Поздравление: {OCCASION_LABELS.get(st.occasion, st.occasion)}"
            send_greeting_email(contact, subject, st.generated_text, st.generated_image)
            confirm = f"✅ Поздравление отправлено на {contact}"
        else:
            image_url = _latest_image_url(db, st.generated_image)
            max_client.send_message(int(contact), st.generated_text, image_url=image_url)
            who = recipient_label or f"chat_id={contact}"
            confirm = f"✅ Поздравление отправлено в MAX → {who}"
    except EmailError as e:
        max_client.send_message(st.chat_id, f"❌ {e}")
        return
    except Exception as e:
        max_client.send_message(st.chat_id, f"❌ Не удалось отправить: {_short(e)}")
        return

    img_id = None
    if st.generated_image:
        hosted = HostedImage(content=st.generated_image)
        db.add(hosted)
        db.flush()
        img_id = hosted.id
    db.add(SentGreeting(
        user_id=st.user_id,
        sender_user_id=st.user_id,
        occasion=st.occasion,
        custom_occasion=st.custom_occasion or "",
        style=st.style,
        channel=st.channel,
        recipient_contact=contact,
        recipient_info=st.recipient_info or "",
        extra_wish=st.extra_wish or "",
        text=st.generated_text,
        has_image=1 if st.generated_image else 0,
        image_id=img_id,
    ))
    st.step = "after_send"
    db.commit()
    max_client.send_message(
        st.chat_id,
        confirm + "\n\nХочешь переслать это же поздравление ещё кому-то или собрать новое?",
        buttons=_after_send_buttons(),
    )


def _short(e: Exception) -> str:
    s = str(e)
    return s if len(s) < 120 else s[:117] + "..."


_DATETIME_PATTERNS = [
    (re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})\s*$"), "dmyt"),
    (re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$"), "dmy"),
    (re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})\s*$"), "dmt"),
    (re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\s*$"), "dm"),
]


def _parse_datetime(s: str) -> Optional[datetime]:
    now = datetime.now()
    for rx, kind in _DATETIME_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        try:
            if kind == "dmyt":
                d, mo, y, h, mi = map(int, m.groups())
                return datetime(y, mo, d, h, mi)
            if kind == "dmy":
                d, mo, y = map(int, m.groups())
                return datetime(y, mo, d, 9, 0)
            if kind == "dmt":
                d, mo, h, mi = map(int, m.groups())
                year = now.year
                candidate = datetime(year, mo, d, h, mi)
                if candidate <= now:
                    candidate = datetime(year + 1, mo, d, h, mi)
                return candidate
            if kind == "dm":
                d, mo = map(int, m.groups())
                year = now.year
                candidate = datetime(year, mo, d, 9, 0)
                if candidate <= now:
                    candidate = datetime(year + 1, mo, d, 9, 0)
                return candidate
        except ValueError:
            return None
    return None


def _show_scheduled(st: UserState, db: Session, max_client: MaxClient) -> None:
    items = (
        db.query(ScheduledGreeting)
        .filter(ScheduledGreeting.user_id == st.user_id, ScheduledGreeting.status == "pending")
        .order_by(ScheduledGreeting.scheduled_at.asc())
        .all()
    )
    if not items:
        max_client.send_message(st.chat_id, "⏰ Запланированных поздравлений нет.")
        return
    lines = [f"⏰ У тебя {len(items)} запланированных поздравлений:\n"]
    for it in items:
        occ = it.custom_occasion or OCCASION_LABELS.get(it.occasion, it.occasion or "—")
        when = it.scheduled_at.strftime("%d.%m.%Y %H:%M")
        ch = "MAX" if it.channel == "max" else "email"
        lines.append(f"• {when} → {ch}: {it.recipient_contact} ({occ})")
    max_client.send_message(st.chat_id, "\n".join(lines))


def process_due_scheduled(db: Session, max_client: MaxClient) -> dict:
    """Find pending ScheduledGreeting with scheduled_at <= now, send them, mark sent/failed.
    Called on every webhook and via /admin/tick. Returns summary dict."""
    now = datetime.now()
    due = (
        db.query(ScheduledGreeting)
        .filter(ScheduledGreeting.scheduled_at <= now, ScheduledGreeting.status == "pending")
        .all()
    )
    sent = 0
    failed = 0
    for item in due:
        try:
            if item.channel == "email":
                img_bytes = None
                if item.image_id:
                    h = db.query(HostedImage).filter(HostedImage.id == item.image_id).first()
                    if h:
                        img_bytes = h.content
                subject = f"Поздравление: {item.custom_occasion or OCCASION_LABELS.get(item.occasion, item.occasion or '')}"
                send_greeting_email(item.recipient_contact, subject, item.text, img_bytes)
            else:
                img_url = None
                if item.image_id:
                    base = (settings.public_base_url or "").rstrip("/")
                    if base:
                        img_url = f"{base}/image/{item.image_id}.jpg"
                max_client.send_message(int(item.recipient_contact), item.text, image_url=img_url)
            item.status = "sent"
            item.sent_at = now
            sent += 1
            # Запись в SentGreeting для истории
            from app.models import SentGreeting
            db.add(SentGreeting(
                user_id=item.user_id,
                sender_user_id=item.user_id,
                occasion=item.occasion,
                custom_occasion=item.custom_occasion or "",
                style=item.style,
                channel=item.channel,
                recipient_contact=item.recipient_contact,
                recipient_info=item.recipient_info or "",
                extra_wish="",  # ScheduledGreeting не хранит это поле
                text=item.text,
                has_image=1 if item.image_id else 0,
                image_id=item.image_id,
            ))
            # уведомляем отправителя
            try:
                max_client.send_message(
                    item.chat_id,
                    f"📤 Запланированное поздравление ({item.scheduled_at.strftime('%d.%m %H:%M')}) успешно отправлено → {item.recipient_contact}",
                )
            except Exception:
                pass
        except Exception as e:
            item.status = "failed"
            item.error = str(e)[:500]
            failed += 1
            try:
                max_client.send_message(
                    item.chat_id,
                    f"⚠️ Не удалось отправить запланированное поздравление на {item.recipient_contact}: {str(e)[:150]}",
                )
            except Exception:
                pass
    if due:
        db.commit()
    return {"processed": len(due), "sent": sent, "failed": failed}


def _show_history(st: UserState, db: Session, max_client: MaxClient) -> None:
    items = (
        db.query(SentGreeting)
        .filter(SentGreeting.user_id == st.user_id)
        .order_by(SentGreeting.id.desc())
        .limit(10)
        .all()
    )
    if not items:
        max_client.send_message(
            st.chat_id,
            "📜 История пуста. Отправь хотя бы одно поздравление — появится здесь.",
            buttons=_occasion_buttons(),
        )
        return

    max_client.send_message(st.chat_id, f"📜 Твои последние {len(items)} поздравлений:")
    base = (settings.public_base_url or "").rstrip("/")
    for it in items:
        occ_label = it.custom_occasion or OCCASION_LABELS.get(it.occasion, it.occasion or "—")
        style_label = STYLE_LABELS.get(it.style, it.style or "—")
        channel_label = {
            "max": "MAX",
            "max_contact": "MAX (себе)",
            "max_self": "MAX (себе)",
            "bot_user": "MAX (коллеге из бота)",
            "email": "email",
            "saved": "📦 только в истории",
        }.get(it.channel, it.channel or "—")
        when = it.created_at.strftime("%d.%m.%Y %H:%M") if it.created_at else "—"

        details = [f"🗓 {when}"]
        details.append(f"🎉 Повод: {occ_label}")
        details.append(f"🎭 Стиль: {style_label}")
        if it.recipient_info:
            details.append(f"👤 Получатель: {it.recipient_info}")
        if it.extra_wish:
            details.append(f"✨ Пожелание: {it.extra_wish}")
        if it.channel == "saved":
            details.append(f"📮 Канал: {channel_label} (без отправки)")
        else:
            details.append(f"📮 Отправлено в {channel_label}: {it.recipient_contact or '—'}")
        details.append("")
        details.append(it.text or "—")

        img_url = None
        if it.image_id and base:
            img_url = f"{base}/image/{it.image_id}.jpg"
        max_client.send_message(st.chat_id, "\n".join(details), image_url=img_url)

    max_client.send_message(
        st.chat_id,
        "Что дальше?",
        buttons=[[
            {"type": "callback", "text": "🆕 Новое поздравление", "payload": "restart"},
            {"type": "callback", "text": "🏁 Завершить", "payload": "finish"},
        ]],
    )


def _host_image(db: Session, binary: bytes) -> Optional[str]:
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        log.warning("PUBLIC_BASE_URL not set — cannot host image URL")
        return None
    img = HostedImage(content=binary)
    db.add(img)
    db.commit()
    db.refresh(img)
    return f"{base}/image/{img.id}.jpg"


def _latest_image_url(db: Session, binary: Optional[bytes]) -> Optional[str]:
    if not binary:
        return None
    return _host_image(db, binary)
