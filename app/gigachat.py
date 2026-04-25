import random
import time
import uuid
import re
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

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
        """Извлекает file_id из ответа GigaChat"""
        match = re.search(r'src="([a-f0-9-]+)"', text)
        if match:
            return match.group(1)
        match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', text)
        if match:
            return match.group(1)
        return None
        
# Простейший "перевод" — транслитерация
def transliterate(text: str) -> str:
    mapping = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
        'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i',
        'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
        'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
        'у': 'u', 'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch',
        'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '',
        'э': 'e', 'ю': 'yu', 'я': 'ya',
    }
    return ''.join(mapping.get(c, c) for c in text.lower())
    
  def build_image_prompt(
    self, 
    occasion: str,           # то, что выбрал пользователь (кнопка)
    style: str,              # стиль (Официальный/Дружеский/С юмором)
    recipient_info: str = "",# информация о получателе (если есть)
    custom_occasion: str = "",# если пользователь ввёл свой повод
    regen_counter: int = 0   # для разнообразия при перегенерации
) -> str:
    """
    Склеивает переменные в простую фразу на английском.
    Без словарей, без ограничений по списку поводов.
    """
    
    # Что именно празднуем
    if custom_occasion:
        topic = custom_occasion
    else:
        topic = occasion
    
    # Перевод стиля на английский
    style_en = {
        "Официальный": "official professional",
        "Тёплый / семейный": "warm family",
        "Корпоративный": "corporate business",
        "С юмором": "humorous funny",
        "Дружеский": "friendly warm",
    }.get(style, "beautiful")
    
    # Собираем фразу из того, что есть
    parts = []
    
    # Основа: что это за картинка
    parts.append(f"Greeting card for {topic}")
    
    # Добавляем стиль
    parts.append(f"style {style_en}")
    
    # Если есть информация о получателе — добавляем
    if recipient_info:
        parts.append(f"for {recipient_info}")
    
    # Добавляем рандомное слово для разнообразия (не влияет на смысл)
    random_words = ["", "celebration", "festive", "happy", "colorful", "elegant"]
    import random
    if regen_counter > 0:
        random.seed(regen_counter)
    rnd = random.choice(random_words)
    if rnd:
        parts.append(rnd)
    
    # Финальная склейка
    prompt = " ".join(parts) + ". No text on image."
    
    self.logger.info(f"Image prompt: {prompt}")
    return prompt
        
        self.logger.info(f"Unique prompt (seed={seed}): {prompt}")
        return prompt

    def generate_image(self, prompt: str, timeout: float = 60, retries: int = 3) -> Optional[GigaChatImage]:
        """
        Генерирует картинку по промпту.
        """
        http_timeout = httpx.Timeout(connect=15, read=timeout, write=30, pool=5)
        
        for attempt in range(retries):
            try:
                token = self._get_token()
                
                with httpx.Client(verify=False, timeout=http_timeout) as c:
                    response = c.post(
                        CHAT_URL,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "GigaChat-2-Max",
                            "messages": [
                                {"role": "system", "content": "Ты — профессиональный иллюстратор. Создаёшь красивые поздравительные открытки. На картинке не должно быть текста."},
                                {"role": "user", "content": f"Нарисуй открытку: {prompt}"}
                            ],
                            "function_call": "auto",
                            "temperature": 1.3,  # Высокая вариативность
                            "max_tokens": 250,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    content = data["choices"][0]["message"].get("content", "")
                    self.logger.info(f"Response (attempt {attempt+1}): {content[:300]}")
                    
                    file_id = self._extract_file_id(content)
                    
                    if not file_id:
                        # Проверяем function_call
                        msg = data["choices"][0]["message"]
                        if "function_call" in msg and msg["function_call"].get("name") == "text2image":
                            args = msg["function_call"].get("arguments", {})
                            if isinstance(args, dict) and "file_id" in args:
                                file_id = args["file_id"]
                            elif isinstance(args, str):
                                try:
                                    import json
                                    args_dict = json.loads(args)
                                    file_id = args_dict.get("file_id")
                                except:
                                    pass
                    
                    if not file_id:
                        self.logger.warning(f"No file_id found, attempt {attempt+1}")
                        continue
                    
                    self.logger.info(f"File_id found: {file_id}")
                    
                    img_response = c.get(
                        f"{FILES_URL}/{file_id}/content",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    img_response.raise_for_status()
                    
                    return GigaChatImage(
                        file_id=file_id,
                        binary=img_response.content,
                        content_type="image/jpeg"
                    )
                    
            except Exception as e:
                self.logger.warning(f"Attempt {attempt+1} failed: {e}")
                if attempt == retries - 1:
                    return None
                time.sleep(2)
        
        return None

    def generate_text(self, system: str, user: str, max_tokens: int = 700) -> str:
        """Генерация текста"""
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
