"""Получение проверенных исторических фактов о текущей дате из русской Википедии.
Wikipedia REST API: /api/rest_v1/feed/onthisday/events/{MM}/{DD}
"""
import logging
import random
from datetime import datetime
from typing import Optional

import httpx

log = logging.getLogger("facts")

URL_TEMPLATE = "https://ru.wikipedia.org/api/rest_v1/feed/onthisday/events/{mm:02d}/{dd:02d}"


def fetch_today_fact() -> Optional[str]:
    """Возвращает один случайный проверенный факт о сегодняшней дате на русском.
    Формат: «<год>: <текст>». Если API недоступен — None."""
    today = datetime.now()
    return _fetch_for_date(today.month, today.day)


def _fetch_for_date(month: int, day: int) -> Optional[str]:
    url = URL_TEMPLATE.format(mm=month, dd=day)
    try:
        with httpx.Client(timeout=8) as c:
            r = c.get(url, headers={"User-Agent": "max-greeting-bot/1.0 (kazan-municipality)"})
            if r.status_code != 200:
                log.warning("wiki onthisday returned %s", r.status_code)
                return None
            data = r.json()
    except Exception as e:
        log.warning("wiki onthisday fetch failed: %s", e)
        return None

    events = data.get("events") or []
    if not events:
        return None

    # выбираем случайный среди относительно «свежих и заметных» — отсекаем слишком древние,
    # они часто туманны для популярного восприятия
    notable = [e for e in events if isinstance(e.get("year"), int) and e["year"] >= 1800]
    if not notable:
        notable = events
    chosen = random.choice(notable[: min(15, len(notable))])
    year = chosen.get("year")
    text = (chosen.get("text") or "").strip()
    if not text:
        return None
    text = text[:220]  # ограничиваем длину
    if year:
        return f"{year}: {text}"
    return text
