from datetime import datetime

# (key, label, (from_mm_dd, to_mm_dd) or None if always-on)
# даты указаны включительно, диапазон «обёрнут вокруг нового года» обрабатывается ниже
OCCASIONS_CONFIG = [
    # Всегда доступные (личные/рабочие)
    ("birthday", "🎂 День рождения", None),
    ("thanks", "🙏 Благодарность коллеге", None),
    ("promotion", "📈 Повышение", None),
    ("work_anniversary", "🏆 Рабочий юбилей", None),
    ("new_colleague", "🤝 Новый коллега", None),
    ("project_success", "🚀 Успех проекта", None),
    ("motivation", "☀️ Мотивация коллегам", None),
    # Государственные праздники РФ
    ("new_year", "🎄 Новый год", ((12, 1), (1, 14))),
    ("mar8", "🌷 8 марта", ((2, 15), (3, 10))),
    ("feb23", "🎖 23 февраля", ((1, 25), (2, 25))),
    ("victory_day", "🕊 День Победы", ((4, 25), (5, 12))),
    ("russia_day", "🇷🇺 День России", ((6, 1), (6, 15))),
    ("flag_day", "🏴 День флага РФ", ((8, 15), (8, 25))),
    ("knowledge_day", "📚 День знаний", ((8, 25), (9, 5))),
    ("teacher_day", "🍎 День учителя", ((9, 28), (10, 8))),
    ("unity_day", "🇷🇺 День народного единства", ((10, 28), (11, 6))),
    ("constitution_day", "📜 День Конституции РФ", ((12, 8), (12, 14))),
    ("programmer_day", "💻 День программиста", ((9, 1), (9, 20))),
    # Региональные — Татарстан
    ("tatar_language_day", "📖 День родного языка Татарстана", ((4, 20), (4, 30))),
    ("tatarstan_day", "🌾 День Республики Татарстан", ((8, 25), (9, 1))),
    ("tatarstan_constitution", "📜 День Конституции РТ", ((11, 1), (11, 8))),
    ("sabantuy", "🎪 Сабантуй", ((6, 10), (6, 30))),
    ("kazan_day", "🕌 День города Казани", ((8, 25), (9, 1))),
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
    "thanks": "рукопожатие, тёплые светлые тона, символ благодарности",
    "promotion": "восходящий график, золотая звезда, уверенный современный стиль",
    "work_anniversary": "медаль с лентой, серпантин, тёплые корпоративные тона",
    "new_colleague": "открытые двери в офис, приветственный букет, свежие оттенки",
    "project_success": "финиш, флаг на вершине, командный рывок, яркие цвета",
    "motivation": "рассвет, кофе, распахнутое окно, вдохновляющая атмосфера",
    "new_year": "ёлка с игрушками, снег, огни гирлянд, праздничная атмосфера",
    "mar8": "весенний букет мимоз и тюльпанов, нежные пастельные тона",
    "feb23": "звезда на фоне триколора, торжественно, сдержанно",
    "victory_day": "гвоздики и георгиевская лента, торжественные сдержанные тона",
    "russia_day": "триколор, силуэт архитектуры, праздничное настроение",
    "flag_day": "развевающийся триколор на фоне неба",
    "knowledge_day": "школьные атрибуты, книги, осенние листья",
    "teacher_day": "букет осенних цветов, раскрытая книга, тёплый свет",
    "unity_day": "многообразие людей, добрые тона, символ единства",
    "constitution_day": "книга законов, российская символика, торжественно",
    "programmer_day": "стилизованный код на экране, абстракция, технологичные синие тона",
    "tatar_language_day": "татарский орнамент, книга, мягкие восточные тона",
    "tatarstan_day": "колосья пшеницы и национальный орнамент, тёплая гамма",
    "tatarstan_constitution": "флаг Татарстана, торжественная композиция",
    "sabantuy": "национальные гуляния, ковёр, сабантуй-атрибутика, яркие цвета",
    "kazan_day": "силуэт Казанского Кремля и мечети Кул-Шариф, праздничные огни",
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
