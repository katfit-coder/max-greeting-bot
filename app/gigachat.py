import base64
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
FILES_URL = "https://gigachat.devices.sberbank.ru/api/v1/files"


@dataclass
class GigaChatImage:
    file_id: str
    binary: bytes
    content_type: str = "image/jpeg"


class GigaChatClient:
    def __init__(self, auth_key: str, scope: str = "GIGACHAT_API_PERS"):
        if not auth_key:
            raise ValueError("GIGACHAT_AUTH_KEY is required")
        self.auth_key = auth_key
        self.scope = scope
        self._token: Optional[str] = None
        self._token_exp: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        with httpx.Client(verify=False, timeout=20) as c:
            r = c.post(
                OAUTH_URL,
                headers={
                    "Authorization": f"Basic {self.auth_key}",
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"scope": self.scope},
            )
            r.raise_for_status()
            data = r.json()
        self._token = data["access_token"]
        expires_at = data.get("expires_at", 0)
        if expires_at > 1e12:
            self._token_exp = expires_at / 1000
        else:
            self._token_exp = time.time() + 1700
        return self._token

    def generate_text(self, system: str, user: str, max_tokens: int = 700) -> str:
        token = self._get_token()
        with httpx.Client(verify=False, timeout=60) as c:
            r = c.post(
                CHAT_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "model": "GigaChat-2-Max",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.8,
                    "max_tokens": max_tokens,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()

    def generate_image(self, description: str, timeout: float = 150, retries: int = 2) -> Optional[GigaChatImage]:
        """GigaChat generates images via function_call=auto when model decides it needs one.
        We force it by asking explicitly. Returns the generated image bytes or None."""
        import logging
        log = logging.getLogger("gigachat")

        prompt = (
            f"Создай изображение поздравительной открытки: {description}. "
            "Требования: "
            "1. Без текста и надписей на изображении. "
            "2. Квадратный формат 1024x1024. "
            "3. Яркая, праздничная, профессиональная композиция. "
            "4. Используй красивую цветовую палитру. "
            "Верни ТОЛЬКО изображение, без текстового описания."
        )
        http_timeout = httpx.Timeout(connect=15, read=timeout, write=30, pool=5)

        last_exc: Exception = RuntimeError("no attempts")
        for attempt in range(retries):
            try:
                token = self._get_token()
                with httpx.Client(verify=False, timeout=http_timeout) as c:
                    r = c.post(
                        CHAT_URL,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        json={
                            "model": "GigaChat-2-Max",
                            "messages": [{"role": "user", "content": prompt}],
                            "function_call": "auto",
                        },
                    )
                    r.raise_for_status()
                    data = r.json()
                    content = data["choices"][0]["message"].get("content", "")
                    log.info("GigaChat image response (attempt %d): %s", attempt + 1, content[:500])

                    file_id = _extract_file_id(content)
                    if not file_id:
                        log.warning("No file_id found in GigaChat response: %s", content[:200])
                        return None
                    log.info("Downloading image file_id=%s", file_id)
                    img_r = c.get(
                        f"{FILES_URL}/{file_id}/content",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/jpg"},
                    )
                    img_r.raise_for_status()
                    log.info("Image downloaded, size=%d bytes", len(img_r.content))
                    return GigaChatImage(file_id=file_id, binary=img_r.content)
            except Exception as e:
                last_exc = e
                log.warning("Image generation attempt %d failed: %s", attempt + 1, e)
        raise last_exc


def _extract_file_id(content: str) -> Optional[str]:
    if 'src="' not in content:
        return None
    start = content.find('src="') + len('src="')
    end = content.find('"', start)
    if end <= start:
        return None
    return content[start:end]
