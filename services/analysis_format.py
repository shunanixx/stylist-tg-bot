"""Иконки по пунктам разбора.

Одна emoji на весь ответ бесполезна: разбор — 16 разделов, и глазу нужны
якоря, чтобы найти в стене текста размер или цену. Иконки ставит код, а не
модель: она их то забывает, то сыплет по три на абзац, и вид разбора
скачет от запроса к запросу.
"""

import re

SECTION_ICONS: dict[int, str] = {
    1: "🎯",  # стиль
    2: "🔍",  # подлинность
    3: "💰",  # ценовой сегмент
    4: "🧵",  # состояние
    5: "📐",  # размер под мои параметры
    6: "🌦",  # сезон
    7: "🧅",  # слойность
    8: "🧼",  # уход и стирка
    9: "⏳",  # долговечность
    10: "📈",  # актуальность тренда
    11: "⚖️",  # цена/качество
    12: "👖",  # сочетание со штанами
    13: "👟",  # обувь
    14: "🧢",  # аксессуары
    15: "🧩",  # сочетание с гардеробом
    16: "🏁",  # вердикт — если он неизвестен
}

VERDICT_ICONS: dict[str, str] = {"брать": "✅", "не брать": "❌"}

VERDICT_POINT = 16

# «1. СТИЛЬ», «**5. РАЗМЕР**», «<b>12. СОЧЕТАНИЕ…</b>» — номер и заголовок
_HEADING_RE = re.compile(r"^(?P<indent>\s*)(?P<body>(?:<b>|\*{1,2})?(?P<number>\d{1,2})[.)]\s+(?P<rest>\S.*))$")
_MARKUP_RE = re.compile(r"<[^>]+>|\*+|_+|#+")


def verdict_icon(verdict: str | None) -> str:
    return VERDICT_ICONS.get((verdict or "").strip().lower(), SECTION_ICONS[VERDICT_POINT])


def decorate_sections(text: str, verdict: str | None = None) -> str:
    """Ставит иконку перед номером пункта: «🎯 1. СТИЛЬ».

    Перед номером, а не после — так строка остаётся «N. ЗАГОЛОВОК» для всего,
    что ищет пункты по номеру, и не ломается на разборе без нумерации.
    """
    if not text:
        return text

    lines = text.split("\n")
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if match is None:
            continue
        number = int(match.group("number"))
        if number not in SECTION_ICONS or not _looks_like_heading(match.group("rest")):
            continue

        # Повторный проход не удваивает иконку сам по себе: у уже украшенной
        # строки перед номером стоит emoji, и `_HEADING_RE` требует, чтобы
        # номер шёл сразу после отступа, — такая строка просто не матчится
        # выше. Проверять «иконка есть в строке где-то» нельзя: если модель
        # сама вставила такой же emoji не в начале, пункт остался бы без
        # иконки бота.
        icon = verdict_icon(verdict) if number == VERDICT_POINT else SECTION_ICONS[number]
        lines[index] = f"{match.group('indent')}{icon} {match.group('body')}"

    return "\n".join(lines)


def _looks_like_heading(rest: str) -> bool:
    """Заголовок пункта — капсом. Иначе «12. джинсы тоже подойдут» внутри
    абзаца получил бы иконку раздела.
    """
    cleaned = _MARKUP_RE.sub("", rest).strip()
    first_word = cleaned.split()[0] if cleaned.split() else ""
    letters = [char for char in first_word if char.isalpha()]
    return len(letters) >= 3 and all(char.isupper() for char in letters)
