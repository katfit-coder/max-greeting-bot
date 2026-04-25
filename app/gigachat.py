import base64
import time
import uuid
import re
import logging
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
        self.logger = logging.getLogger("gigachat")

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

    def _extract_file_id(self, text: str) -> Optional[str]:
        """Извлекает file_id из тега <img src=\"...\">"""
        # Ищем src="<uuid>"
        match = re.search(r'src="([a-f0-9-]+)"', text)
        if match:
            return match.group(1)
        # Ищем file_id в любом месте текста
        match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', text)
        if match:
            return match.group(1)
        return None

    def generate_image(self, prompt: str, timeout: float = 90, retries: int = 3) -> Optional[GigaChatImage]:
        """
        Генерирует картинку через GigaChat.
        ВАЖНО: нужно передавать function_call: "auto"!
        """
        http_timeout = httpx.Timeout(connect=15, read=timeout, write=30, pool=5)

        for attempt in range(retries):
            try:
                token = self._get_token()
                
                # Формируем запрос на генерацию картинки
                # Для лучшего результата добавляем английскую версию промпта
                messages = [
                    {
                        "role": "user",
                        "content": f"Нарисуй: {prompt}"
                    }
                ]
                
                # Можно добавить системный промпт для стилизации
                # messages.insert(0, {"role": "system", "content": "Ты — художник-иллюстратор"})
                
                with httpx.Client(verify=False, timeout=http_timeout) as c:
                    response = c.post(
                        CHAT_URL,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        json={
                            "model": "GigaChat-2-Max",
                            "messages": messages,
                            "function_call": "auto",  # ← КЛЮЧЕВОЙ ПАРАМЕТР!
                            "temperature": 0.9,
                            "max_tokens": 300,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    self.logger.info(f"Response (attempt {attempt+1}): {str(data)[:500]}")
                    
                    # Получаем content из ответа
                    content = data["choices"][0]["message"].get("content", "")
                    
                    # Извлекаем file_id
                    file_id = self._extract_file_id(content)
                    
                    if not file_id:
                        # Проверяем также в data_for_context
                        if "data_for_context" in data["choices"][0]["message"]:
                            for ctx in data["choices"][0]["message"]["data_for_context"]:
                                if ctx.get("function_call", {}).get("name") == "text2image":
                                    args = ctx["function_call"].get("arguments", {})
                                    if isinstance(args, dict) and "file_id" in args:
                                        file_id = args["file_id"]
                                        break
                        
                    if not file_id:
                        self.logger.warning(f"No file_id found in response: {content[:300]}")
                        continue
                    
                    self.logger.info(f"Found file_id: {file_id}")
                    
                    # Скачиваем изображение
                    img_response = c.get(
                        f"{FILES_URL}/{file_id}/content",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/jpg",
                        },
                    )
                    img_response.raise_for_status()
                    
                    self.logger.info(f"Image downloaded, size={len(img_response.content)} bytes")
                    
                    return GigaChatImage(
                        file_id=file_id,
                        binary=img_response.content,
                        content_type="image/jpeg"
                    )
                    
            except Exception as e:
                self.logger.warning(f"Attempt {attempt+1} failed: {e}")
                if attempt == retries - 1:
                    raise
                time.sleep(2)  # Небольшая пауза перед повторной попыткой
        
        return None

    def generate_text(self, system: str, user: str, max_tokens: int = 700) -> str:
        """Генерация текста через GigaChat"""
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
