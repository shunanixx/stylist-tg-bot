import json
import re
from typing import Any

DATA_MARKER = "===DATA==="
VERDICT_TAKE = "брать"
VERDICT_SKIP = "не брать"
VERDICT_UNKNOWN = "не определено"
DEFAULT_TITLE = "Без названия"

CATEGORIES = ("верхняя одежда", "верх", "низ", "обувь", "аксессуар")

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
# Нежадный: блок данных — плоский однострочный объект без вложенных {}.
# Жадный `.*}` захватывал бы случайную `{`/`}` в тексте после блока (см.
# требование «после JSON-блока никакого текста быть не должно» — модель его
# иногда нарушает) и портил валидный JSON.
_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)


def fallback_data() -> dict[str, Any]:
    return {"title": DEFAULT_TITLE, "verdict": VERDICT_UNKNOWN, "category": None}


def parse_llm_response(raw_text: str | None) -> tuple[str, dict[str, Any]]:
    """Делит ответ модели на текст для пользователя и структурированные данные.

    Единый парсер для всех провайдеров — маркер ===DATA=== работает везде,
    в отличие от нативного JSON-mode, который есть не у всех.
    """
    text = (raw_text or "").strip()
    if not text:
        return "", fallback_data()

    if DATA_MARKER not in text:
        return text, fallback_data()

    # rsplit: если модель упомянула маркер в тексте анализа, данные — последний блок
    display_text, _, data_block = text.rpartition(DATA_MARKER)
    data = _parse_data_block(data_block)
    return display_text.strip(), data


def _parse_data_block(block: str) -> dict[str, Any]:
    cleaned = _FENCE_RE.sub("", block.strip()).strip().strip("`").strip()
    if cleaned.startswith("json"):
        cleaned = cleaned[4:].strip()

    payload = _load_json(cleaned)
    if payload is None:
        match = _OBJECT_RE.search(cleaned)
        payload = _load_json(match.group(0)) if match else None
    if not isinstance(payload, dict):
        return fallback_data()

    return {
        "title": _normalize_title(payload.get("title")),
        "verdict": normalize_verdict(payload.get("verdict")),
        "category": _normalize_category(payload.get("category")),
    }


def _load_json(candidate: str) -> Any:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _normalize_title(value: Any) -> str:
    title = str(value).strip() if value is not None else ""
    return title or DEFAULT_TITLE


def normalize_verdict(value: Any) -> str:
    """Приводит вердикт к «брать» / «не брать» / «не определено».

    Проверка отрицания идёт первой: «НЕ БРАТЬ» содержит подстроку «брать».
    """
    if value is None:
        return VERDICT_UNKNOWN
    text = str(value).strip().lower().replace("ё", "е")
    if not text:
        return VERDICT_UNKNOWN
    if "не брать" in text or "не стоит" in text:
        return VERDICT_SKIP
    if "брать" in text:
        return VERDICT_TAKE
    return VERDICT_UNKNOWN


def _normalize_category(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    for category in CATEGORIES:
        if category in text:
            return category
    return text
