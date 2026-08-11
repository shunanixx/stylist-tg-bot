"""Обхват против полуобхвата: ввод, удвоение, отказ там, где его быть не может."""

import pytest

from services.measurements import (
    BY_FIELD,
    MEASUREMENTS,
    describe,
    format_measurement,
    looks_like_half,
    parse_value,
)

CHEST = BY_FIELD["chest_cm"]
HEIGHT = BY_FIELD["height_cm"]
SHOULDERS = BY_FIELD["shoulders_cm"]


@pytest.mark.parametrize("raw", ["96", " 96 ", "96.0", "96,0"])
def test_plain_number_is_girth_as_is(raw):
    assert parse_value(raw, CHEST) == 96


@pytest.mark.parametrize("raw", ["пол 48", "пол48", "п48", "полуобхват 48", "48/2"])
def test_half_marker_doubles_value(raw):
    """Продавец пишет полуобхват — храним всегда обхват."""
    assert parse_value(raw, CHEST) == 96


def test_fractional_half_survives():
    assert parse_value("пол 42.5", BY_FIELD["belt_cm"]) == 85


@pytest.mark.parametrize("measurement", [HEIGHT, SHOULDERS])
def test_half_rejected_where_girth_is_meaningless(measurement):
    """Молча удвоить рост — хуже, чем отказать: ошибка всплывёт в пункте 5."""
    assert parse_value("пол 90", measurement) is None


@pytest.mark.parametrize("raw", ["метр восемьдесят", "", "-", "abc"])
def test_non_numeric_rejected(raw):
    assert parse_value(raw, CHEST) is None


def test_looks_like_half_separates_marker_error_from_garbage():
    assert looks_like_half("пол 90") is True
    assert looks_like_half("90") is False
    assert looks_like_half("пол много") is False


def test_describe_adds_half_only_for_girth():
    assert describe(CHEST, 96) == "Обхват груди 96 см (полуобхват 48)"
    assert describe(HEIGHT, 175) == "Рост 175 см"
    assert describe(SHOULDERS, 44) == "Ширина плеч 44 см"


def test_every_girth_range_admits_adult_body():
    """Диапазоны должны принимать обхват, а не полуобхват: 96 см груди — норма."""
    for measurement in MEASUREMENTS:
        if measurement.girth:
            assert measurement.low <= 96 <= measurement.high, measurement.field


@pytest.mark.parametrize(
    ("value", "expected"), [(175.0, "175"), (42.5, "42.5"), (85, "85")]
)
def test_format_measurement(value, expected):
    assert format_measurement(value) == expected
