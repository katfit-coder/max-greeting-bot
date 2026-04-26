from typing import Optional

import httpx

BASE_URL = "https://botapi.max.ru"


class MaxClient:
    """Client for MAX bot API. Token goes in Authorization header only (query param deprecated)."""

    def __init__(self, token: str):
        if not token:
            raise ValueError("MAX_BOT_TOKEN is required")
        self.token = token

    def _url(self, path: str) -> str:
        return f"{BASE_URL}{path}"

    def _headers(self, with_content_type: bool = True) -> dict:
        h = {"Authorization": self.token}
        if with_content_type:
            h["Content-Type"] = "application/json"
        return h

    def send_message(
        self,
        chat_id: int,
        text: str,
        buttons: Optional[list[list[dict]]] = None,
        image_bytes: Optional[bytes] = None,
        image_url: Optional[str] = None,
    ) -> dict:
        """Отправляем сообщение в MAX. Если есть image_bytes — заливаем через /uploads
        (штатный путь MAX); если есть только image_url — пробуем по ссылке (fallback).
        Длинный caption + image + keyboard MAX иногда отклоняет «Failed to upload image»,
        поэтому caller-у лучше разносить длинный текст и картинку с кнопками на 2 сообщения."""
        import logging
        log = logging.getLogger("max_client")
        attachments = []

        if image_bytes is not None:
            token = self._upload_image(image_bytes)
            if token:
                attachments.append({"type": "image", "payload": {"token": token}})
            elif image_url:
                # upload через MAX не получился — пробуем по URL
                log.warning("MAX upload failed, falling back to image_url")
                attachments.append({"type": "image", "payload": {"url": image_url}})
        elif image_url:
            attachments.append({"type": "image", "payload": {"url": image_url}})

        if buttons:
            attachments.append({
                "type": "inline_keyboard",
                "payload": {"buttons": buttons},
            })
        payload: dict = {"text": text[:4000] if text else " "}
        if attachments:
            payload["attachments"] = attachments

        try:
            with httpx.Client(timeout=30) as c:
                r = c.post(
                    self._url("/messages"),
                    params={"chat_id": chat_id},
                    headers=self._headers(),
                    json=payload,
                )
                if r.status_code >= 400:
                    log.error(
                        "MAX send_message %s: chat_id=%s body=%s payload=%s",
                        r.status_code, chat_id, r.text[:500], payload,
                    )
                r.raise_for_status()
                return r.json()
        except Exception as e:
            log.error(f"MAX send_message failed: chat_id={chat_id}, error={e}", exc_info=False)
            raise

    def answer_callback(self, callback_id: str, notification: str = "") -> dict:
        with httpx.Client(timeout=10) as c:
            r = c.post(
                self._url("/answers"),
                params={"callback_id": callback_id},
                headers=self._headers(),
                json={"notification": notification} if notification else {},
            )
            return r.json() if r.status_code < 400 else {"error": r.text}

    def _upload_image(self, binary: bytes) -> Optional[str]:
        """Двухшаговая загрузка картинки в MAX:
        1. POST /uploads?type=image → {url}
        2. POST <url> с multipart-файлом → {token}
        Возвращает token, если всё ок, иначе None."""
        import logging
        log = logging.getLogger("max_client")
        try:
            with httpx.Client(timeout=60) as c:
                r = c.post(
                    self._url("/uploads"),
                    params={"type": "image"},
                    headers=self._headers(with_content_type=False),
                )
                if r.status_code >= 400:
                    log.warning("MAX /uploads failed: %s %s", r.status_code, r.text[:300])
                    return None
                data = r.json()
                upload_url = data.get("url")
                if not upload_url:
                    log.warning("MAX /uploads no url in response: %s", data)
                    return None

                up = c.post(
                    upload_url,
                    files={"data": ("card.jpg", binary, "image/jpeg")},
                )
                if up.status_code >= 400:
                    log.warning("MAX upload binary failed: %s %s", up.status_code, up.text[:300])
                    return None

                # Ответ может быть в нескольких форматах — пробуем все варианты
                try:
                    j = up.json()
                except Exception:
                    log.warning("MAX upload returned non-JSON: %s", up.text[:300])
                    return None

                token = (
                    j.get("token")
                    or (j.get("photos") or {}).get("photo_id")
                    or (j.get("data") or {}).get("token")
                )
                if not token:
                    log.warning("MAX upload: no token in response: %s", j)
                return token
        except Exception as e:
            log.warning("MAX _upload_image exception: %s", e)
            return None

    def subscribe_webhook(self, url: str) -> dict:
        with httpx.Client(timeout=15) as c:
            r = c.post(
                self._url("/subscriptions"),
                headers=self._headers(),
                json={"url": url, "update_types": ["message_created", "message_callback", "bot_started", "bot_added"]},
            )
            return {"status": r.status_code, "body": r.text}

    def list_subscriptions(self) -> dict:
        with httpx.Client(timeout=15) as c:
            r = c.get(self._url("/subscriptions"), headers=self._headers())
            return {"status": r.status_code, "body": r.text}

    def set_commands(self, commands: list[dict]) -> dict:
        """Регистрирует меню команд в чате (которое появляется в поле ввода).
        commands: [{"name": "start", "description": "Начать заново"}, ...]
        Если MAX не поддерживает — отвечает 4xx, мы это просто логируем."""
        # API MAX патчит профиль бота — там есть поле commands
        with httpx.Client(timeout=15) as c:
            r = c.patch(
                self._url("/me"),
                headers=self._headers(),
                json={"commands": commands},
            )
            return {"status": r.status_code, "body": r.text[:300]}
