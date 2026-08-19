"""Рекомендация размера одежды по параметрам фигуры."""

from services.measurements import suggest_sizes


class FakeUser:
    def __init__(self, chest=None, waist=None, belt=None):
        self.chest_cm = chest
        self.waist_cm = waist
        self.belt_cm = belt


def test_suggests_size_for_average_woman():
    """Обхват груди 92 см = S-M в большинстве систем."""
    user = FakeUser(chest=92)

    result = suggest_sizes(user)

    assert "S" in result or "M" in result
    assert "🇪🇺" in result
    assert "🇺🇦" in result
    assert "🇺🇸" in result


def test_uses_max_girth():
    """Если талия шире груди — ориентируемся на талию."""
    user = FakeUser(chest=84, waist=100, belt=92)

    result = suggest_sizes(user)

    assert "M" in result or "L" in result


def test_suggests_range_when_in_between():
    """Если обхват близко к границе размера — показываем оба."""
    user = FakeUser(chest=92)  # На границе S-M

    result = suggest_sizes(user)

    assert ("S" in result and "M" in result) or result.count("/") > 0


def test_handles_empty_measurements():
    """Без обхватов выводит подсказку, а не крашится."""
    user = FakeUser(chest=None, waist=None, belt=None)

    result = suggest_sizes(user)

    assert "Заполните" in result


def test_picks_closest_if_outside_range():
    """Очень маленький/большой обхват — выбираем ближайший размер."""
    small = FakeUser(chest=60)  # Меньше даже XS
    large = FakeUser(chest=200)  # Больше XXL

    small_result = suggest_sizes(small)
    large_result = suggest_sizes(large)

    assert "XS" in small_result
    assert "XXL" in large_result
