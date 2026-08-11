import pytest

from services.response_parser import (
    DEFAULT_TITLE,
    VERDICT_SKIP,
    VERDICT_TAKE,
    VERDICT_UNKNOWN,
    parse_llm_response,
)


def test_parses_clean_data_block():
    raw = (
        "1. СТИЛЬ: streetwear\n16. ВЕРДИКТ: **БРАТЬ**\n"
        '===DATA===\n{"title": "Куртка Carhartt Detroit", "verdict": "брать", '
        '"category": "верхняя одежда"}'
    )
    text, data = parse_llm_response(raw)

    assert text.startswith("1. СТИЛЬ")
    assert "===DATA===" not in text
    assert data == {
        "title": "Куртка Carhartt Detroit",
        "verdict": VERDICT_TAKE,
        "category": "верхняя одежда",
    }


def test_strips_markdown_fence():
    raw = 'Анализ\n===DATA===\n```json\n{"title": "Кеды", "verdict": "не брать", "category": "обувь"}\n```'
    _, data = parse_llm_response(raw)

    assert data["verdict"] == VERDICT_SKIP
    assert data["title"] == "Кеды"


def test_negation_wins_over_substring():
    """«НЕ БРАТЬ» содержит «брать» — отрицание должно проверяться первым."""
    _, data = parse_llm_response('x\n===DATA===\n{"verdict": "НЕ БРАТЬ", "title": "t"}')
    assert data["verdict"] == VERDICT_SKIP


def test_uses_last_marker_when_repeated():
    raw = 'Формат такой: ===DATA===\nразбор\n===DATA===\n{"title": "Худи", "verdict": "брать"}'
    text, data = parse_llm_response(raw)

    assert data["title"] == "Худи"
    assert text.endswith("разбор")


def test_missing_marker_returns_fallback():
    text, data = parse_llm_response("Просто текст без блока данных")

    assert text == "Просто текст без блока данных"
    assert data == {"title": DEFAULT_TITLE, "verdict": VERDICT_UNKNOWN, "category": None}


def test_broken_json_falls_back_but_keeps_text():
    text, data = parse_llm_response("Разбор\n===DATA===\n{сломанный json")

    assert text == "Разбор"
    assert data["verdict"] == VERDICT_UNKNOWN


def test_json_with_trailing_prose_is_extracted():
    raw = 'Разбор\n===DATA===\n{"title": "Джинсы", "verdict": "брать"} — вот и всё'
    _, data = parse_llm_response(raw)

    assert data["title"] == "Джинсы"
    assert data["verdict"] == VERDICT_TAKE


@pytest.mark.parametrize("raw", ["", None, "   "])
def test_empty_input(raw):
    text, data = parse_llm_response(raw)

    assert text == ""
    assert data["verdict"] == VERDICT_UNKNOWN
