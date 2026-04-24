OCCASIONS = [
    ("birthday", "🎂 День рождения"),
    ("new_year", "🎄 Новый год"),
    ("mar8", "🌷 8 марта"),
    ("feb23", "🎖 23 февраля"),
    ("programmer_day", "💻 День программиста"),
    ("thanks", "🙏 Благодарность коллеге"),
]

STYLES = [
    ("official", "Официальный"),
    ("warm", "Тёплый / семейный"),
    ("corporate", "Корпоративный"),
    ("humor", "С юмором"),
    ("friendly", "Дружеский"),
]

OCCASION_LABELS = dict(OCCASIONS)
STYLE_LABELS = dict(STYLES)


TEXT_SYSTEM = (
    "Ты — ассистент-копирайтер в муниципальной организации (Мэрия Казани). "
    "Пишешь краткие, уместные и живые поздравления на русском языке. "
    "Никогда не используешь штампы вроде «в этот прекрасный день» или «пусть сбудутся все мечты». "
    "Никогда не вставляешь эмодзи избыточно — максимум 2-3 на всё сообщение. "
    "Вывод — только текст поздравления, без вступлений вроде «Вот ваше поздравление:»."
)


def build_text_prompt(occasion_key: str, style_key: str, extra_wish: str = "",
                      recipient_name: str = "", sender_name: str = "",
                      recipient_info: str = "", custom_occasion: str = "") -> str:
    from datetime import datetime
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


def build_image_prompt(occasion_key: str, style_key: str) -> str:
    base = IMAGE_DESCRIPTIONS.get(occasion_key, "праздничная поздравительная композиция")
    hint = STYLE_VISUAL_HINTS.get(style_key, "")
    return f"открытка-поздравление: {base}, {hint}, без текста и надписей на изображении, высокое качество"
