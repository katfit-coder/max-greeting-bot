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

    def _transliterate(self, text: str) -> str:
        """Простейшая транслитерация для GigaChat"""
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
        occasion: str,
        style: str,
        recipient_info: str = "",
        custom_occasion: str = "",
        regen_counter: int = 0
    ) -> str:
        """
        Склеивает переменные в простую фразу на английском.
        """
        # Что именно празднуем
        if custom_occasion:
            topic = custom_occasion
        else:
            topic = occasion
        
        # Транслитерируем тему (чтобы GigaChat понял)
        topic_en = self._transliterate(topic)
        
        # Перевод стиля на английский
        style_en = {
            "Официальный": "official professional",
            "Тёплый / семейный": "warm family",
            "Корпоративный": "corporate business",
            "С юмором": "humorous funny",
            "Дружеский": "friendly warm",
        }.get(style, "beautiful")
        
        # Собираем фразу
        parts = [f"Greeting card for {topic_en}", f"style {style_en}"]
        
        if recipient_info:
            recipient_en = self._transliterate(recipient_info)
            parts.append(f"for {recipient_en}")
        
        # Рандомное слово для разнообразия
        random_words = ["celebration", "festive", "happy", "colorful", "elegant"]
        if regen_counter > 0:
            random.seed(regen_counter)
        rnd = random.choice(random_words)
        parts.append(rnd)
        
        prompt = " ".join(parts) + ". No text on image."
        
        self.logger.info(f"Image prompt: {prompt}")
        return prompt

    def generate_image(self, prompt: str, timeout: float = 60, retries: int = 3) -> Optional[GigaChatImage]:
        """Генерирует картинку по промпту."""
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
                                {"role": "system", "content": "You are an illustrator. Create beautiful greeting cards. No text on images."},
                                {"role": "user", "content": f"Draw: {prompt}"}
                            ],
                            "function_call": "auto",
                            "temperature": 1.2,
                            "max_tokens": 250,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    content = data["choices"][0]["message"].get("content", "")
                    self.logger.info(f"Response (attempt {attempt+1}): {content[:300]}")
                    
                    file_id = self._extract_file_id(content)
                    
                    if not file_id:
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

    def compose_image_scene(
        self, occasion_label: str, style_label: str,
        recipient_info: str = "", extra_wish: str = "",
        custom_occasion: str = "",
    ) -> str:
        """Универсальный «арт-директор»: придумывает уникальную сцену под открытку
        исключительно на основе входов. Без per-occasion веток."""
        token = self._get_token()
        topic = custom_occasion or occasion_label

        sys_prompt = (
            "Ты — креативный арт-директор поздравительных открыток. "
            "На входе ты получаешь: повод поздравления, стиль и опционально контекст о получателе. "
            "На выходе: ОДНА уникальная сцена для иллюстрации в 1–2 предложениях (40–70 слов). "
            "Жёсткие правила: "
            "1) Сцена должна на 100% соответствовать данному поводу — не подмешивай элементы из других праздников и не используй универсальные клише поздравительных открыток (торт со свечами, воздушные шары, цифры возраста, букеты роз, фейерверк), если повод сам не подразумевает их буквально. "
            "2) В сцене НЕ должно быть людей: ни мужчин, ни женщин, ни лиц, ни фигур, ни силуэтов, ни рук. Только предметы, символы, природа, интерьер, абстракция. "
            "3) Контекст получателя используй косвенно — через предметы профессии или интересов (например: «бухгалтер» → весы, папки; «программист» → ноутбук, код), но не описывай самого человека. "
            "4) Каждый раз выбирай разный визуальный приём: интерьер, метафора, характерный предмет, пейзаж, абстракция, минимализм, натюрморт. "
            "5) На изображении не должно быть НИКАКОГО текста, букв, цифр, надписей, слоганов или ярлыков. "
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
