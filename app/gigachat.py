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

    def compose_image_scene(
        self, occasion_label: str, style_label: str,
        recipient_info: str = "", extra_wish: str = "",
        custom_occasion: str = "",
    ) -> str:
        """Универсальный арт-директор. ОДИН промпт для всех поводов, никаких per-occasion веток.
        Сцена строится только из переданного текста повода + контекста получателя."""
        token = self._get_token()
        topic = custom_occasion or occasion_label

        sys_prompt = (
            "Ты — креативный арт-директор поздравительных открыток. "
            "На входе ты получаешь: повод поздравления, стиль и опционально контекст о получателе и пожелание. "
            "Твоя задача: придумать ОДНУ уникальную сцену для иллюстрации в 1–2 предложениях (40–70 слов). "
            "Жёсткие правила: "
            "1) Сцена должна на 100% соответствовать данному поводу — не подмешивай элементы из других праздников и не используй универсальные клише поздравительных открыток (торт со свечами, воздушные шары, цифры возраста, букеты роз, фейерверк), если повод сам не подразумевает их буквально. "
            "2) Используй контекст получателя для индивидуальной детали. "
            "3) Каждый раз выбирай разный визуальный приём: интерьер, метафора, характерный предмет, руки крупным планом, пейзаж, абстракция, минимализм. "
            "4) На изображении не должно быть НИКАКОГО текста, букв, цифр, надписей, слоганов или ярлыков. "
            "Выдай только сам текст описания сцены, без вступлений, кавычек или инструкций."
        )
        user_prompt_parts = [f"Повод: {topic}.", f"Стиль: {style_label}."]
        if recipient_info:
            user_prompt_parts.append(f"О получателе: {recipient_info}.")
        if extra_wish:
            user_prompt_parts.append(f"Пожелание: {extra_wish}.")
        user_prompt_parts.append("Опиши уникальную сцену открытки.")
        user_prompt = " ".join(user_prompt_parts)

        with httpx.Client(verify=False, timeout=httpx.Timeout(connect=15, read=40, write=20, pool=5)) as c:
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
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 1.0,
                    "max_tokens": 220,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()

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

    def generate_image(self, description: str, timeout: float = 120, retries: int = 2) -> Optional[GigaChatImage]:
        """GigaChat generates images via function_call=auto when model decides it needs one.
        `description` — уже готовый промпт из prompts.build_image_prompt."""
        import logging
        log = logging.getLogger("gigachat")
        # отказываемся от раздувания описания (сам build_image_prompt уже делает всё что нужно)
        prompt = description
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
