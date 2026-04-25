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

    def build_unique_image_prompt(
        self, 
        occasion: str,           # повод
        style: str,              # стиль
        extra: str = "",         # дополнительные условия от пользователя
        recipient_name: str = "",# имя получателя
        seed: int = None         # для разнообразия при перегенерации
    ) -> str:
        """
        Строит УНИКАЛЬНЫЙ промпт для картинки, учитывая ВСЕ параметры.
        Промпт короткий (20-40 слов), чтобы быстро генерировался.
        """
        if seed is None:
            seed = random.randint(1, 100000)
        random.seed(seed)
        
        # Словари для случайного выбора (каждый раз разные комбинации)
        subjects = {
            "День рождения": ["торт", "подарок", "конфетти", "праздничная вечеринка", "сладкий стол", "шампанское"],
            "8 марта": ["тюльпаны", "мимоза", "весенние цветы", "букет", "цветущий сад", "нежные лепестки"],
            "23 февраля": ["звезда", "георгиевская лента", "военная техника", "флаг", "салют", "парад"],
            "Новый год": ["ёлка", "снежинки", "подарки", "шампанское", "фейерверк", "Дед Мороз"],
            "День программиста": ["ноутбук", "код на экране", "клавиатура", "сервер", "бинарный код", "компьютер"],
            "Благодарность": ["сердце", "рукопожатие", "лучи солнца", "цветы", "тёплый свет", "улыбка"],
            "Повышение": ["лестница", "звезда", "диплом", "офис с видом", "успех", "медаль"],
            "Успех проекта": ["ракета", "флаг на вершине", "фейерверк", "победа", "команда", "шампанское"],
            "Мотивация": ["солнце", "горы", "дорога", "свет в конце туннеля", "рассвет", "вдохновение"],
        }
        
        # Базовые объекты, если повода нет в словаре
        default_subjects = ["праздник", "поздравление", "подарок", "радость", "торжество"]
        
        # Модификаторы для уникальности (добавляем рандомные детали)
        modifiers = [
            "в мягких тонах", "с блеском", "в минимализме", "в акварели", 
            "в цифровой живописи", "в ретро-стиле", "с лёгкой дымкой", 
            "на светлом фоне", "в пастельных цветах", "с яркими акцентами",
            "в тёплых оттенках", "в холодных тонах", "с золотым свечением"
        ]
        
        # Выбираем случайный объект под повод
        subject_list = subjects.get(occasion, default_subjects)
        main_object = random.choice(subject_list)
        
        # Если есть дополнительные условия от пользователя — учитываем их
        extra_element = ""
        if extra:
            # Выбираем ключевое слово из пожелания
            extra_keywords = {
                "здоровье": "здоровье, свежесть, жизненная сила",
                "счастье": "радость, улыбки, солнечный свет",
                "успех": "достижения, победа, триумф",
                "любовь": "сердца, нежность, романтика",
                "деньги": "монеты, богатство, достаток",
                "путешествие": "чемодан, карта, приключения",
                "семья": "дом, уют, близкие",
                "работа": "офис, коллеги, карьера",
            }
            for kw, value in extra_keywords.items():
                if kw in extra.lower():
                    extra_element = f", {value}"
                    break
            if not extra_element:
                extra_element = f", отражающее настроение: {extra[:30]}"
        
        # Стиль открытки
        style_map = {
            "Официальный": "строгий деловой стиль",
            "Тёплый / семейный": "уютный домашний стиль",
            "Корпоративный": "современный корпоративный стиль",
            "С юмором": "весёлый мультяшный стиль",
            "Дружеский": "дружеская тёплая иллюстрация",
        }
        style_text = style_map.get(style, "красивый праздничный стиль")
        
        # Имя получателя (легко влияет на уникальность)
        name_hint = f" для {recipient_name}" if recipient_name else ""
        
        # Рандомный модификатор
        modifier = random.choice(modifiers)
        
        # Собираем финальный промпт (короткий! максимум 40 слов)
        prompt = (
            f"Поздравительная открытка: {main_object} в честь {occasion.lower()}"
            f"{extra_element}{name_hint}. "
            f"Стиль: {style_text}. "
            f"Рисунок {modifier}. Без текста и надписей."
        )
        
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
