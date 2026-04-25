import base64
import random
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
        """
        Генерирует уникальный промпт для открытки (30-60 слов, быстро).
        Учитывает повод, стиль, информацию о получателе и пожелание.
        """
        token = self._get_token()
        topic = custom_occasion or occasion_label

        sys_prompt = (
            "Ты создаёшь уникальное визуальное описание для поздравительной открытки. "
            "Правила: "
            "1) Никогда не используй торты, шары, цветы, свечи, фейерверки, цифры возраста — это клише. "
            "2) Придумай оригинальную метафору или неожиданную сцену (руки, интерьер, природа, абстракция, предметы). "
            "3) Учитывай повод и контекст о получателе для персонализации. "
            "4) На картинке НЕ должно быть текста, букв, цифр, надписей. "
            "5) Каждый раз выбирай разный визуальный приём. "
            "Ответ — одно предложение, 30-60 слов, на русском языке."
        )
        
        user_prompt_parts = [f"Повод: {topic}.", f"Стиль: {style_label}."]
        if recipient_info:
            user_prompt_parts.append(f"О получателе: {recipient_info}.")
        if extra_wish:
            user_prompt_parts.append(f"Пожелание: {extra_wish}.")
        user_prompt_parts.append("Опиши уникальную сцену для открытки.")
        user_prompt = " ".join(user_prompt_parts)

        with httpx.Client(verify=False, timeout=httpx.Timeout(connect=10, read=30, write=10, pool=5)) as c:
            r = c.post(
                CHAT_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "GigaChat-2-Max",
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 1.3,      # творческий разброс
                    "max_tokens": 140,       # короткий ответ для скорости
                },
            )
            r.raise_for_status()
            scene = r.json()["choices"][0]["message"]["content"].strip()
            
            # Добавляем случайный художественный стиль для разнообразия
            style_suffixes = [
                " в стиле акварель",
                " минималистичная иллюстрация",
                " мягкие пастельные тона",
                " в стиле цифровой живописи",
                " в стиле карандашного рисунка",
                "",
                "",
            ]
            scene += random.choice(style_suffixes)
            return scene

    def generate_text(self, system: str, user: str, max_tokens: int = 700) -> str:
        token = self._get_token()
        with httpx.Client(verify=False, timeout=60) as c:
            r = c.post(
                CHAT_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
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

    def generate_image(self, description: str, timeout: float = 60, retries: int = 2) -> Optional[GigaChatImage]:
        """
        Генерирует уникальную картинку на основе промпта.
        Оптимизирован для скорости (таймаут 60 сек) и вариативности.
        """
        import logging
        log = logging.getLogger("gigachat")
        
        # Оставляем промпт как есть — не добавляем лишнего
        prompt = description
        http_timeout = httpx.Timeout(connect=10, read=timeout, write=20, pool=5)

        last_exc = RuntimeError("no attempts")
        for attempt in range(retries):
            try:
                token = self._get_token()
                with httpx.Client(verify=False, timeout=http_timeout) as c:
                    r = c.post(
                        CHAT_URL,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "GigaChat-2-Max",
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 1.2,      # высокая вариативность
                            "max_tokens": 300,
                            "function_call": "none",  # отключаем авто-функции
                        },
                    )
                    r.raise_for_status()
                    data = r.json()
                    content = data["choices"][0]["message"].get("content", "")
                    log.info("GigaChat response (attempt %d): %s", attempt + 1, content[:300])

                    file_id = self._extract_file_id(content)
                    if not file_id:
                        log.warning("No file_id found in response: %s", content[:200])
                        continue
                    
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
                log.warning("Attempt %d failed: %s", attempt + 1, e)
                
        raise last_exc

    @staticmethod
    def _extract_file_id(content: str) -> Optional[str]:
        """Извлекает file_id из ответа GigaChat"""
        if 'src="' not in content:
            return None
        start = content.find('src="') + len('src="')
        end = content.find('"', start)
        if end <= start:
            return None
        return content[start:end]
