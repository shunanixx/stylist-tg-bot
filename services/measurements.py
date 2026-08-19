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


@dataclass(frozen=True)
class SizeRange:
    """Размер и его диапазон обхватов в см."""

    size: str
    chest_min: float
    chest_max: float


# Стандартные европейские размеры по обхвату груди
EU_SIZES = (
    SizeRange("XS (EU 32)", 76, 84),
    SizeRange("S (EU 36)", 84, 92),
    SizeRange("M (EU 40)", 92, 100),
    SizeRange("L (EU 44)", 100, 108),
    SizeRange("XL (EU 48)", 108, 116),
    SizeRange("XXL (EU 50+)", 116, 200),
)

US_SIZES = (
    SizeRange("XS (US 0-2)", 76, 84),
    SizeRange("S (US 4-6)", 84, 92),
    SizeRange("M (US 8-10)", 92, 100),
    SizeRange("L (US 12-14)", 100, 108),
    SizeRange("XL (US 16-18)", 108, 116),
    SizeRange("XXL (US 20+)", 116, 200),
)

# Украина обычно использует EU размеры
UA_SIZES = EU_SIZES


def suggest_sizes(user) -> str:
    """Рекомендует размер по параметрам. Показывает диапазон и ближайший."""
    chest = getattr(user, "chest_cm", None)
    waist = getattr(user, "waist_cm", None)
    belt = getattr(user, "belt_cm", None)

    # Ориентируемся на максимальный обхват — обычно при выборе размера
    # вещи смотрят, чтобы она не облегала в самом широком месте
    girths = [v for v in [chest, waist, belt] if v is not None]
    if not girths:
        return "📏 Заполните все обхваты, чтобы узнать рекомендуемый размер"

    main_girth = max(girths)

    lines = ["📏 <b>Рекомендуемые размеры</b>\n"]
    for systems, name in [(EU_SIZES, "🇪🇺 Europe"), (UA_SIZES, "🇺🇦 Ukraina"), (US_SIZES, "🇺🇸 USA")]:
        # Диапазон: все размеры, в которые попадает основной обхват
        in_range = [s.size for s in systems if s.chest_min <= main_girth < s.chest_max]
        # Ближайший: если не попадает, выбираем самый близкий
        if in_range:
            suggested = " / ".join(in_range)
        else:
            closest = min(systems, key=lambda s: abs((s.chest_min + s.chest_max) / 2 - main_girth))
            suggested = closest.size

        lines.append(f"{name}: {suggested}")

    return "\n".join(lines)
