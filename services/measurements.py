"""Единый источник правды по замерам фигуры.

Ключевое различие: обхват меряется лентой вокруг тела, полуобхват — вещь,
разложенная по плоскости (грудь «от шва до шва»). Продавцы на ресейле почти
всегда указывают полуобхваты, а человек знает свои обхваты, поэтому модели
нужны оба числа — иначе пункт 5 сравнивает несравнимое и ошибается вдвое.
"""

from dataclasses import dataclass

# «пол 42.5», «п42.5», «42.5/2» — все формы означают полуобхват
HALF_MARKERS = ("полуобхват", "пол", "п", "/2")


@dataclass(frozen=True)
class Measurement:
    field: str
    label: str
    unit: str
    question: str
    low: float
    high: float
    # Обхват допускает полуобхват; рост, вес и ширина плеч — нет
    girth: bool = False


MEASUREMENTS: tuple[Measurement, ...] = (
    Measurement("height_cm", "Рост", "см", "📏 Рост в см?", 100, 250),
    Measurement("weight_kg", "Вес", "кг", "⚖️ Вес в кг?", 30, 250),
    Measurement(
        "shoulders_cm",
        "Ширина плеч",
        "см",
        "📐 Ширина плеч в см — по спине от плеча до плеча.\n"
        "Это ширина, а не обхват: полуобхвата у неё нет.",
        25,
        70,
    ),
    Measurement(
        "chest_cm",
        "Обхват груди",
        "см",
        "📐 Обхват груди в см — <b>полный обхват</b>, лентой вокруг тела "
        "по самой широкой точке.\n"
        "Если под рукой только вещь по плоскости, пришли полуобхват так: «пол 48».",
        60,
        160,
        girth=True,
    ),
    Measurement(
        "waist_cm",
        "Обхват талии",
        "см",
        "📐 Обхват талии в см — <b>полный обхват</b>, лентой вокруг "
        "по самому узкому месту.\n"
        "Полуобхват — «пол 38».",
        50,
        150,
        girth=True,
    ),
    Measurement(
        "belt_cm",
        "Обхват пояса",
        "см",
        "📐 Обхват пояса в см — <b>полный обхват</b> там, где сидят джинсы.\n"
        "Полуобхват — «пол 42.5».",
        50,
        160,
        girth=True,
    ),
)

BY_FIELD: dict[str, Measurement] = {m.field: m for m in MEASUREMENTS}


def format_measurement(value: float | int) -> str:
    """175.0 → «175», 42.5 → «42.5» — чтобы промпт не пестрел лишними нулями."""
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def half(value: float) -> str:
    return format_measurement(value / 2)


def describe(measurement: Measurement, value: float) -> str:
    """«Обхват груди 96 см (полуобхват 48)» — обе величины сразу."""
    base = f"{measurement.label} {format_measurement(value)} {measurement.unit}"
    if not measurement.girth:
        return base
    return f"{base} (полуобхват {half(value)})"


def _strip_half_marker(text: str) -> tuple[str, bool]:
    for marker in HALF_MARKERS:
        if text.startswith(marker):
            return text[len(marker) :].strip(), True
        if text.endswith(marker):
            return text[: -len(marker)].strip(), True
    return text, False


def looks_like_half(raw: str) -> bool:
    """Пользователь пытался прислать полуобхват — для точной подсказки об ошибке."""
    text, is_half = _strip_half_marker(raw.strip().lower().replace(",", "."))
    if not is_half:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


def parse_value(raw: str, measurement: Measurement) -> float | None:
    """Возвращает обхват. Полуобхват с маркером удваивается.

    None — если это не число: вызывающий код сам решает, что ответить.
    """
    text, is_half = _strip_half_marker(raw.strip().lower().replace(",", "."))

    try:
        value = float(text)
    except ValueError:
        return None

    if is_half:
        if not measurement.girth:
            # У роста и ширины плеч полуобхвата не бывает — молча удвоить хуже,
            # чем отвергнуть: пользователь узнает об ошибке сразу.
            return None
        value *= 2
    return value
