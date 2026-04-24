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
    ) -> dict:
        attachments = []
        if image_bytes is not None:
            token = self._upload_image(image_bytes)
            if token:
                attachments.append({"type": "image", "payload": {"token": token}})
        if buttons:
            attachments.append({
                "type": "inline_keyboard",
                "payload": {"buttons": buttons},
            })
        payload: dict = {"text": text[:4000] if text else " "}
        if attachments:
            payload["attachments"] = attachments

        with httpx.Client(timeout=30) as c:
            r = c.post(
                self._url("/messages"),
                params={"chat_id": chat_id},
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()
            return r.json()

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
        """Two-step upload: get upload URL, POST binary, return token."""
        with httpx.Client(timeout=60) as c:
            r = c.post(
                self._url("/uploads"),
                params={"type": "image"},
                headers=self._headers(),
            )
            if r.status_code >= 400:
                return None
            data = r.json()
            upload_url = data.get("url")
            if not upload_url:
                return None
            up = c.post(upload_url, files={"data": ("card.jpg", binary, "image/jpeg")})
            if up.status_code >= 400:
                return None
            try:
                j = up.json()
                token = j.get("token") or j.get("photos", {}).get("photo_id")
            except Exception:
                token = None
            return token

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
