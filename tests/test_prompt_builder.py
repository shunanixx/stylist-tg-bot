from types import SimpleNamespace

import pytest

from services.prompt_builder import PromptBuilder, format_measurement


@pytest.fixture
def builder():
    return PromptBuilder()


def make_user(**kwargs):
    # Обхваты, не полуобхваты: в БД хранится обхват, полуобхват выводится делением
    defaults = {
        "height_cm": 175,
        "weight_kg": 55,
        "shoulders_cm": 44,
        "chest_cm": 96,
        "waist_cm": 84,
        "belt_cm": 85,
    }
    return SimpleNamespace(**{**defaults, **kwargs})


def test_includes_methodology_and_output_format(builder):
    prompt = builder.build(make_user(), [], [])

    assert "16. ВЕРДИКТ" in prompt
    assert "===DATA===" in prompt
    assert prompt.index("[МОИ ПАРАМЕТРЫ]") < prompt.index("[ИНСТРУКЦИЯ ПО ФОРМАТУ ВЫВОДА]")


def test_measurements_rendered_without_trailing_zeros(builder):
    prompt = builder.build(make_user(), [], [])

    assert "Рост 175 см" in prompt
    assert "Обхват пояса 85 см (полуобхват 42.5)" in prompt
    assert "175.0" not in prompt


def test_girth_measurements_carry_half_value(builder):
    """Замеры вещи у продавцов — в полуобхвате, поэтому нужны оба числа."""
    prompt = builder.build(make_user(), [], [])

    assert "Обхват груди 96 см (полуобхват 48)" in prompt
    assert "Обхват талии 84 см (полуобхват 42)" in prompt
    # Рост и ширина плеч — не обхваты, полуобхвата у них быть не должно
    assert "Рост 175 см (полуобхват" not in prompt
    assert "Ширина плеч 44 см (полуобхват" not in prompt


def test_missing_measurements_are_flagged(builder):
    prompt = builder.build(make_user(height_cm=None, weight_kg=None), [], [])

    assert "Рост" not in prompt.split("[МОЙ ГАРДЕРОБ]")[0].split("[МОИ ПАРАМЕТРЫ]")[1]
    assert "Ширина плеч 44 см" in prompt


def test_no_user_at_all_asks_model_not_to_guess(builder):
    prompt = builder.build(None, [], [])

    assert "не указаны" in prompt


def test_wardrobe_items_with_details(builder):
    items = [
        SimpleNamespace(title="Серый свитшот", color=None, size="S"),
        SimpleNamespace(title="Тёмно-зелёная куфта Mango", color="зелёный", size="M"),
        "Adidas широкие штаны клёш",
    ]
    prompt = builder.build(make_user(), items, [])

    assert "- Серый свитшот (S)" in prompt
    assert "- Тёмно-зелёная куфта Mango (зелёный, M)" in prompt
    assert "- Adidas широкие штаны клёш" in prompt


def test_empty_wardrobe_instructs_point_15(builder):
    prompt = builder.build(make_user(), [], [])

    assert "пункте 15" in prompt


def test_recent_verdicts_block(builder):
    recent = [
        SimpleNamespace(item_title="Кеды Puma", final_verdict="не брать"),
        ("Худи Champion", "брать"),
    ]
    prompt = builder.build(make_user(), [], recent)

    assert "[ПОСЛЕДНИЕ ВЕРДИКТЫ]" in prompt
    assert "- Кеды Puma: не брать" in prompt
    assert "- Худи Champion: брать" in prompt


def test_verdicts_block_omitted_when_empty(builder):
    assert "[ПОСЛЕДНИЕ ВЕРДИКТЫ]" not in builder.build(make_user(), [], [])


def test_incomplete_verdicts_are_skipped(builder):
    recent = [SimpleNamespace(item_title="Кофта", final_verdict=None)]

    assert "[ПОСЛЕДНИЕ ВЕРДИКТЫ]" not in builder.build(make_user(), [], recent)


@pytest.mark.parametrize(
    ("value", "expected"), [(175, "175"), (175.0, "175"), (42.5, "42.5"), (42.50, "42.5")]
)
def test_format_measurement(value, expected):
    assert format_measurement(value) == expected
