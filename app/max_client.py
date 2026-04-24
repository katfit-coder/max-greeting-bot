from typing import Optional

import httpx

BASE_URL = "https://botapi.max.ru"


class MaxClient:
    """Tiny client for MAX bot API.

    The API base URL and exact param names aren't 100% locked in the docs excerpt,
    so we defensively pass token both as header AND as `access_token` query param
    (older examples in the wild use the query). Either route works.
    """

    def __init__(self, token: str):
        if not token:
            raise ValueError("MAX_BOT_TOKEN is required")
        self.token = token

    def _url(self, path: str) -> str:
        return f"{BASE_URL}{path}"

    def _headers(self) -> dict:
        return {"Authorization": self.token, "Content-Type": "application/json"}

    def _params(self, extra: Optional[dict] = None) -> dict:
        p = {"access_token": self.token}
        if extra:
            p.update(extra)
        return p

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
                params=self._params({"chat_id": chat_id}),
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    def answer_callback(self, callback_id: str, notification: str = "") -> dict:
        with httpx.Client(timeout=10) as c:
            r = c.post(
                self._url("/answers"),
                params=self._params({"callback_id": callback_id}),
                json={"notification": notification} if notification else {},
            )
            return r.json() if r.status_code < 400 else {"error": r.text}

    def _upload_image(self, binary: bytes) -> Optional[str]:
        """Two-step upload: get upload URL, PUT/POST binary, return token."""
        with httpx.Client(timeout=60) as c:
            r = c.post(
                self._url("/uploads"),
                params=self._params({"type": "image"}),
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
                token = up.json().get("token") or up.json().get("photos", {}).get("photo_id")
            except Exception:
                token = None
            return token

    def subscribe_webhook(self, url: str) -> dict:
        with httpx.Client(timeout=15) as c:
            r = c.post(
                self._url("/subscriptions"),
                params=self._params(),
                json={"url": url, "update_types": ["message_created", "message_callback"]},
            )
            return {"status": r.status_code, "body": r.text}

    def list_subscriptions(self) -> dict:
        with httpx.Client(timeout=15) as c:
            r = c.get(self._url("/subscriptions"), params=self._params())
            return {"status": r.status_code, "body": r.text}
