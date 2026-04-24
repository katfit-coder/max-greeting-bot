from datetime import datetime

# (key, label, (from_mm_dd, to_mm_dd) or None if always-on)
# даты указаны включительно, диапазон «обёрнут вокруг нового года» обрабатывается ниже
OCCASIONS_CONFIG = [
    ("birthday", "🎂 День рождения", None),                         # всегда
    ("new_year", "🎄 Новый год", ((12, 1), (1, 14))),               # декабрь-начало января
    ("mar8", "🌷 8 марта", ((2, 15), (3, 10))),
    ("feb23", "🎖 23 февраля", ((1, 25), (2, 25))),
    ("programmer_day", "💻 День программиста", ((9, 1), (9, 20))),
    ("thanks", "🙏 Благодарность коллеге", None),                   # всегда
]

STYLES = [
    ("official", "Официальный"),
    ("warm", "Тёплый / семейный"),
    ("corporate", "Корпоративный"),
    ("humor", "С юмором"),
    ("friendly", "Дружеский"),
]

OCCASION_LABELS = {k: label for k, label, _ in OCCASIONS_CONFIG}
STYLE_LABELS = dict(STYLES)


def _in_range(today_mm_dd: tuple[int, int], start: tuple[int, int], end: tuple[int, int]) -> bool:
    """Inclusive range check. Supports ranges that wrap around new year (e.g. Dec 1 → Jan 14)."""
    if start <= end:
        return start <= today_mm_dd <= end
    # wrap: today is either >= start (same-year tail) or <= end (next-year head)
    return today_mm_dd >= start or today_mm_dd <= end


def current_available_occasions() -> list[tuple[str, str]]:
    """Return list of (key, label) for occasions valid today. Birthday/thanks/custom always included."""
    now = datetime.now()
    today = (now.month, now.day)
    result = []
    for key, label, rng in OCCASIONS_CONFIG:
        if rng is None or _in_range(today, rng[0], rng[1]):
            result.append((key, label))
    return result


# ============================================================================
# Корпоративный контекст Мэрии Казани — используется в official/corporate стилях
# ============================================================================
KAZAN_CONTEXT = (
    "Контекст: поздравление в Мэрии Казани — команде, которая развивает город. "
    "Слоган: «Создаём город, в котором хочется жить, работать и мечтать!». "
    "Ценности: служение городу и людям, наследие и культура как сила, "
    "гибкость подходов при прочности целей, человекоцентричность, профессиональная команда, "
    "наставничество, реализация масштабных проектов. "
    "Для корпоративных поздравлений уместно мягко сослаться на общее дело — "
    "развитие Казани и заботу о жителях — без пафоса и штампов."
)


TEXT_SYSTEM = (
    "Ты — ассистент-копирайтер в Мэрии Казани. "
    "Пишешь краткие, уместные и живые поздравления на русском языке. "
    "Никогда не используешь штампы вроде «в этот прекрасный день» или «пусть сбудутся все мечты». "
    "Эмодзи — максимум 2-3 на всё сообщение. "
    "Вывод — только текст поздравления, без вступлений вроде «Вот ваше поздравление:»."
)


def build_text_prompt(occasion_key: str, style_key: str, extra_wish: str = "",
                      recipient_name: str = "", sender_name: str = "",
                      recipient_info: str = "", custom_occasion: str = "") -> str:
    today = datetime.now().strftime("%d.%m")

    if occasion_key == "custom" and custom_occasion:
        occasion = custom_occasion
    else:
        occasion = OCCASION_LABELS.get(occasion_key, occasion_key)

    style = STYLE_LABELS.get(style_key, style_key)
    parts = [
        f"Составь поздравление. Повод: {occasion}. Стиль: {style}.",
        f"Сегодняшняя дата: {today}.",
        "Объём: 3–5 предложений, до 550 символов.",
    ]
    # корпоративные ценности применяются в подходящих стилях
    if style_key in ("corporate", "official"):
        parts.append(KAZAN_CONTEXT)
    if recipient_info:
        parts.append(f"Персональный контекст о получателе (используй деликатно, если уместно): {recipient_info}.")
    if recipient_name:
        parts.append(f"Обращение к получателю: {recipient_name}.")
    if sender_name:
        parts.append(f"Подпись от: {sender_name}.")
    if extra_wish:
        parts.append(f"Учти дополнительное пожелание: {extra_wish}.")
    parts.append(
        "В самом конце через перенос строки одной короткой фразой упомяни, "
        "какой ещё праздник или заметное историческое событие отмечается сегодня в России "
        "(если такое есть), оформи как «P.S. Кстати, ...». Если ничего заметного нет — P.S. опусти."
    )
    parts.append("Верни только чистый текст поздравления, без кавычек и пояснений.")
    return " ".join(parts)


IMAGE_DESCRIPTIONS = {
    "birthday": "праздничный торт со свечами, шары, тёплые золотистые тона",
    "new_year": "ёлка с игрушками, снег, огни гирлянд, праздничная атмосфера",
    "mar8": "весенний букет мимоз и тюльпанов, нежные пастельные тона",
    "feb23": "звезда на фоне триколора, торжественно, сдержанно",
    "programmer_day": "стилизованный код на экране, абстракция, технологичные синие тона",
    "thanks": "рукопожатие, тёплые светлые тона, символ благодарности",
}

STYLE_VISUAL_HINTS = {
    "official": "минималистично, сдержанная цветовая гамма",
    "warm": "тёплый свет, мягкие акварельные мазки",
    "corporate": "современный корпоративный стиль, чистая геометрия",
    "humor": "мультяшный весёлый стиль, яркие цвета",
    "friendly": "дружеская иллюстрация, акварель",
}


def build_image_prompt(occasion_key: str, style_key: str, custom_occasion: str = "") -> str:
    if occasion_key == "custom" and custom_occasion:
        base = f"поздравительная композиция на тему «{custom_occasion}»"
    else:
        base = IMAGE_DESCRIPTIONS.get(occasion_key, "праздничная поздравительная композиция")
    hint = STYLE_VISUAL_HINTS.get(style_key, "")
    return f"открытка-поздравление: {base}, {hint}, без текста и надписей на изображении, высокое качество"


# для обратной совместимости со старыми импортами
OCCASIONS = [(k, label) for k, label, _ in OCCASIONS_CONFIG]
