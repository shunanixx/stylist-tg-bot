from services.analysis_format import (
    SECTION_ICONS,
    decorate_sections,
    verdict_icon,
)

ANALYSIS = """1. СТИЛЬ
Ближе всего к минимализму.

5. РАЗМЕР
Полуобхват груди 48 — сядет.

16. ВЕРДИКТ
Брать."""


def test_every_point_gets_its_own_icon():
    text = "\n".join(f"{number}. ЗАГОЛОВОК" for number in range(1, 17))
    decorated = decorate_sections(text, "брать")
    for number in range(1, 16):
        assert f"{SECTION_ICONS[number]} {number}. ЗАГОЛОВОК" in decorated


def test_icon_stands_before_the_number():
    """Номер должен остаться началом заголовка: по «N. НАЗВАНИЕ» текст ищут
    и тесты разбора, и пользователь глазами."""
    decorated = decorate_sections(ANALYSIS)
    assert "🎯 1. СТИЛЬ" in decorated
    assert "1. СТИЛЬ" in decorated
    assert "📐 5. РАЗМЕР" in decorated


def test_verdict_point_takes_the_verdict_icon():
    assert "✅ 16. ВЕРДИКТ" in decorate_sections(ANALYSIS, "брать")
    assert "❌ 16. ВЕРДИКТ" in decorate_sections(ANALYSIS, "не брать")


def test_unknown_verdict_falls_back_to_the_neutral_flag():
    assert "🏁 16. ВЕРДИКТ" in decorate_sections(ANALYSIS, None)
    assert "🏁 16. ВЕРДИКТ" in decorate_sections(ANALYSIS, "непонятно")


def test_verdict_icon_ignores_case_and_spaces():
    assert verdict_icon(" Брать ") == "✅"
    assert verdict_icon("НЕ БРАТЬ") == "❌"
    assert verdict_icon("") == SECTION_ICONS[16]


def test_second_pass_does_not_double_the_icon():
    once = decorate_sections(ANALYSIS, "брать")
    assert decorate_sections(once, "брать") == once


def test_own_icon_elsewhere_in_the_line_does_not_block_decoration():
    """Идемпотентность держится на том, что уже украшенная строка не матчится
    заголовком заново — а не на поиске символа иконки где-то в строке. Модель
    сама иногда лепит emoji в заголовок (см. докстринг модуля); если он
    совпал с иконкой раздела, пункт всё равно обязан получить иконку бота."""
    text = "2. ПОДЛИННОСТЬ 🔍 ПРОВЕРЕНО ПО ФОТО\nВсё ок."
    decorated = decorate_sections(text)
    assert decorated.startswith(f"{SECTION_ICONS[2]} 2. ПОДЛИННОСТЬ")


def test_numbers_inside_a_paragraph_stay_untouched():
    text = "12. джинсы тоже подойдут\n3. пара сотен сверху — не критично"
    assert decorate_sections(text) == text


def test_markup_around_the_heading_survives():
    assert "🧵 <b>4. СОСТОЯНИЕ</b>" in decorate_sections("<b>4. СОСТОЯНИЕ</b>")
    assert "💰 **3. ЦЕНА**" in decorate_sections("**3. ЦЕНА**")


def test_points_outside_the_methodology_get_nothing():
    text = "17. ЛИШНИЙ ПУНКТ\n0. НОЛЬ"
    assert decorate_sections(text) == text


def test_empty_text_passes_through():
    assert decorate_sections("") == ""
    assert decorate_sections("Просто текст без пунктов") == "Просто текст без пунктов"
