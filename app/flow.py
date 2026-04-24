import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.emailer import EmailError, send_greeting_email
from app.gigachat import GigaChatClient
from app.max_client import MaxClient
from app.models import SentGreeting, UserState
from app.prompts import (
    OCCASIONS,
    OCCASION_LABELS,
    STYLE_LABELS,
    STYLES,
    TEXT_SYSTEM,
    build_image_prompt,
    build_text_prompt,
)

log = logging.getLogger("flow")

EMAIL_RE = re.compile(r"^[\w.\-+]+@[\w\-]+\.[\w.\-]+$")


def _occasion_buttons() -> list[list[dict]]:
    rows = []
    current: list[dict] = []
    for key, label in OCCASIONS:
        current.append({"type": "callback", "text": label, "payload": f"occasion:{key}"})
        if len(current) == 2:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    return rows


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


def _channel_buttons() -> list[list[dict]]:
    return [
        [
            {"type": "callback", "text": "💬 Отправить в MAX", "payload": "channel:max"},
            {"type": "callback", "text": "✉️ На email", "payload": "channel:email"},
        ],
        [{"type": "callback", "text": "◀️ Отмена", "payload": "cancel"}],
    ]


def _get_or_create_state(db: Session, user_id: int, chat_id: int) -> UserState:
    st = db.query(UserState).filter(UserState.user_id == user_id).first()
    if not st:
        st = UserState(user_id=user_id, chat_id=chat_id, step="idle")
        db.add(st)
        db.commit()
        db.refresh(st)
    else:
        if st.chat_id != chat_id:
            st.chat_id = chat_id
            db.commit()
    return st


def handle_update(update: dict, db: Session, max_client: MaxClient, giga: GigaChatClient) -> None:
    u_type = update.get("update_type") or update.get("type")
    if u_type == "message_created":
        _handle_message(update, db, max_client, giga)
    elif u_type == "message_callback":
        _handle_callback(update, db, max_client, giga)


def _extract_from(obj: dict) -> tuple[Optional[int], Optional[int]]:
    msg = obj.get("message") or {}
    recipient = msg.get("recipient") or {}
    sender = msg.get("sender") or obj.get("user") or {}
    chat_id = recipient.get("chat_id") or obj.get("chat_id")
    user_id = sender.get("user_id") or sender.get("id") or obj.get("user_id")
    return chat_id, user_id


def _handle_message(update: dict, db: Session, max_client: MaxClient, giga: GigaChatClient) -> None:
    msg = update.get("message") or {}
    body = msg.get("body") or {}
    text = (body.get("text") or "").strip()
    chat_id, user_id = _extract_from(update)
    if not chat_id or not user_id:
        log.warning("can't resolve chat_id/user_id from update: %s", update)
        return
    st = _get_or_create_state(db, user_id, chat_id)

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
        items = db.query(SentGreeting).filter(SentGreeting.user_id == user_id).order_by(SentGreeting.id.desc()).limit(5).all()
        if not items:
            max_client.send_message(chat_id, "История пуста. Напиши /start чтобы отправить первое поздравление.")
            return
        lines = ["📜 Последние 5 отправленных поздравлений:\n"]
        for i, it in enumerate(items, 1):
            occ = OCCASION_LABELS.get(it.occasion, it.occasion)
            st_ = STYLE_LABELS.get(it.style, it.style)
            when = it.created_at.strftime("%d.%m %H:%M")
            lines.append(f"{i}. {when} · {occ} ({st_}) → {it.channel}: {it.recipient_contact}")
        max_client.send_message(chat_id, "\n".join(lines))
        return

    if text.lower().startswith("/cancel"):
        st.step = "idle"
        db.commit()
        max_client.send_message(chat_id, "Ок, отменил. Напиши /start чтобы начать заново.")
        return

    if st.step == "await_extra_wish":
        st.extra_wish = text[:300]
        db.commit()
        _generate_and_preview(st, db, max_client, giga)
        return

    if st.step == "await_contact":
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
        return

    if text.lower().startswith("/"):
        max_client.send_message(chat_id, "Команды: /start — новое поздравление · /history — последние отправки · /cancel — отмена")
        return

    max_client.send_message(
        chat_id,
        "Не понял. Напиши /start чтобы начать сбор поздравления, или /history для истории.",
    )


def _handle_callback(update: dict, db: Session, max_client: MaxClient, giga: GigaChatClient) -> None:
    cb = update.get("callback") or {}
    payload = cb.get("payload") or ""
    callback_id = cb.get("callback_id") or ""
    chat_id, user_id = _extract_from(update)
    if not chat_id or not user_id:
        return
    st = _get_or_create_state(db, user_id, chat_id)
    max_client.answer_callback(callback_id)

    if payload.startswith("occasion:"):
        st.occasion = payload.split(":", 1)[1]
        st.step = "choose_style"
        db.commit()
        max_client.send_message(
            chat_id,
            f"Повод: {OCCASION_LABELS.get(st.occasion, st.occasion)}.\nТеперь выбери стиль:",
            buttons=_style_buttons(),
        )
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
        st.step = "await_contact"
        db.commit()
        if st.channel == "email":
            max_client.send_message(chat_id, "📧 Введи email получателя:")
        else:
            max_client.send_message(
                chat_id,
                f"💬 Введи chat_id получателя в MAX (число).\n\nДля теста можешь отправить на свой — твой chat_id: {chat_id}",
            )
        return

    if payload == "cancel":
        st.step = "idle"
        db.commit()
        max_client.send_message(chat_id, "Отменено. /start чтобы начать заново.")
        return


def _generate_and_preview(st: UserState, db: Session, max_client: MaxClient, giga: GigaChatClient) -> None:
    try:
        text = giga.generate_text(
            TEXT_SYSTEM,
            build_text_prompt(st.occasion, st.style, st.extra_wish, st.recipient_name, st.sender_name),
        )
    except Exception as e:
        log.exception("text gen failed")
        max_client.send_message(
            st.chat_id,
            f"⚠️ GigaChat не смог сгенерировать текст ({_short(e)}). Попробуй /start ещё раз через минуту.",
        )
        return

    st.generated_text = text
    db.commit()

    image_bytes: Optional[bytes] = None
    try:
        img = giga.generate_image(build_image_prompt(st.occasion, st.style))
        if img is not None:
            image_bytes = img.binary
            st.generated_image = image_bytes
            db.commit()
    except Exception as e:
        log.warning("image gen failed: %s", e)
        max_client.send_message(st.chat_id, "⚠️ Картинку сгенерировать не удалось, покажу только текст.")

    st.step = "preview"
    db.commit()

    occ = OCCASION_LABELS.get(st.occasion, st.occasion)
    style = STYLE_LABELS.get(st.style, st.style)
    caption = f"📝 Черновик поздравления\n\nПовод: {occ}\nСтиль: {style}\n\n{text}"
    max_client.send_message(st.chat_id, caption, buttons=_preview_buttons(), image_bytes=image_bytes)


def _regen_text(st: UserState, db: Session, max_client: MaxClient, giga: GigaChatClient) -> None:
    try:
        text = giga.generate_text(
            TEXT_SYSTEM,
            build_text_prompt(st.occasion, st.style, st.extra_wish, st.recipient_name, st.sender_name),
        )
    except Exception as e:
        max_client.send_message(st.chat_id, f"⚠️ Не получилось: {_short(e)}")
        return
    st.generated_text = text
    db.commit()
    image_bytes = st.generated_image
    caption = f"📝 Новый вариант:\n\n{text}"
    max_client.send_message(st.chat_id, caption, buttons=_preview_buttons(), image_bytes=image_bytes)


def _regen_image(st: UserState, db: Session, max_client: MaxClient, giga: GigaChatClient) -> None:
    try:
        img = giga.generate_image(build_image_prompt(st.occasion, st.style))
    except Exception as e:
        max_client.send_message(st.chat_id, f"⚠️ Картинку не получилось: {_short(e)}")
        return
    if img is None:
        max_client.send_message(st.chat_id, "⚠️ GigaChat не вернул изображение. Попробуй ещё раз.")
        return
    st.generated_image = img.binary
    db.commit()
    max_client.send_message(
        st.chat_id,
        f"🎨 Новая открытка к тому же тексту:\n\n{st.generated_text}",
        buttons=_preview_buttons(),
        image_bytes=img.binary,
    )


def _send_final(st: UserState, contact: str, db: Session, max_client: MaxClient) -> None:
    try:
        if st.channel == "email":
            subject = f"Поздравление: {OCCASION_LABELS.get(st.occasion, st.occasion)}"
            send_greeting_email(contact, subject, st.generated_text, st.generated_image)
            confirm = f"✅ Поздравление отправлено на {contact}"
        else:
            max_client.send_message(int(contact), st.generated_text, image_bytes=st.generated_image)
            confirm = f"✅ Поздравление отправлено в MAX (chat_id={contact})"
    except EmailError as e:
        max_client.send_message(st.chat_id, f"❌ {e}")
        return
    except Exception as e:
        max_client.send_message(st.chat_id, f"❌ Не удалось отправить: {_short(e)}")
        return

    db.add(SentGreeting(
        user_id=st.user_id,
        occasion=st.occasion,
        style=st.style,
        channel=st.channel,
        recipient_contact=contact,
        text=st.generated_text,
        has_image=1 if st.generated_image else 0,
    ))
    st.step = "idle"
    db.commit()
    max_client.send_message(st.chat_id, confirm + "\n\nНапиши /start чтобы отправить ещё одно.")


def _short(e: Exception) -> str:
    s = str(e)
    return s if len(s) < 120 else s[:117] + "..."
