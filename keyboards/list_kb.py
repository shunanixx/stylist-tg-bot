"""Инлайн-управление списками: гардероб, вишлист, стили.

Кнопки заменяют команды с аргументами: вместо «/style_add минимализм» —
кнопка и ответ обычным текстом, вместо «/remove 3» — кнопка с самой вещью,
чтобы номер не приходилось переписывать из списка и нельзя было промахнуться
мимо позиции. Команды остаются рабочими: кнопка ведёт в ту же логику.
"""

from collections.abc import Callable, Sequence
from typing import Any

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Свой префикс, а не «wardrobe:»: у разбора уже есть «wardrobe:add:<id разбора>»,
# и общий фильтр по имени раздела ловил бы оба потока сразу.
PREFIX = "menu"

STYLES = "styles"
WARDROBE = "wardrobe"
WISHLIST = "wishlist"

ADD = "add"
RENAME = "rename"
DELETE = "del"
BOUGHT = "bought"
CANCEL = "cancel"

CANCEL_TEXT = "↩️ Отмена"
# Длинную подпись Telegram обрезает по-своему — режем сами, чтобы номер и
# начало названия были видны всегда
LABEL_LIMIT = 40
# Больше сорока рядов — это уже не выбор, а простыня: остаток берётся командой
PICK_LIMIT = 40


def callback_data(section: str, action: str, item_id: int | None = None) -> str:
    tail = "" if item_id is None else f":{item_id}"
    return f"{PREFIX}:{section}:{action}{tail}"


def item_prefix(section: str, action: str) -> str:
    """Фильтр для кнопок с вещью: «menu:wardrobe:del:» и только они."""
    return callback_data(section, action) + ":"


def item_id_from(data: str) -> int | None:
    parts = data.split(":")
    return int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None


def actions_kb(section: str, actions: Sequence[tuple[str, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for text, action in actions:
        builder.button(text=text, callback_data=callback_data(section, action))
    builder.adjust(2)
    return builder.as_markup()


def pick_kb(
    section: str,
    action: str,
    items: Sequence[Any],
    label: Callable[[Any], str],
) -> InlineKeyboardMarkup:
    """Кнопка на каждую вещь: номер в подписи тот же, что в списке выше."""
    builder = InlineKeyboardBuilder()
    for position, item in enumerate(items[:PICK_LIMIT], start=1):
        builder.button(
            text=f"{position}. {shorten(label(item))}",
            callback_data=callback_data(section, action, item.id),
        )
    builder.button(text=CANCEL_TEXT, callback_data=callback_data(section, CANCEL))
    builder.adjust(1)
    return builder.as_markup()


def cancel_kb(section: str) -> InlineKeyboardMarkup:
    """Под вопросом «пришли название»: выйти без набора /cancel."""
    builder = InlineKeyboardBuilder()
    builder.button(text=CANCEL_TEXT, callback_data=callback_data(section, CANCEL))
    return builder.as_markup()


def styles_kb(items: Sequence[Any]) -> InlineKeyboardMarkup:
    if not items:
        return actions_kb(STYLES, [("➕ Добавить стиль", ADD)])
    return actions_kb(
        STYLES,
        [("➕ Добавить", ADD), ("✏️ Переименовать", RENAME), ("🗑 Убрать", DELETE)],
    )


def wardrobe_kb(items: Sequence[Any]) -> InlineKeyboardMarkup:
    if not items:
        return actions_kb(WARDROBE, [("➕ Добавить вещь", ADD)])
    return actions_kb(WARDROBE, [("➕ Добавить", ADD), ("🗑 Убрать", DELETE)])


def wishlist_kb(items: Sequence[Any]) -> InlineKeyboardMarkup:
    if not items:
        return actions_kb(WISHLIST, [("➕ Отложить вещь", ADD)])
    return actions_kb(
        WISHLIST,
        [("➕ Отложить", ADD), ("🎉 Куплено", BOUGHT), ("🗑 Убрать", DELETE)],
    )


def shorten(text: str) -> str:
    return text if len(text) <= LABEL_LIMIT else text[: LABEL_LIMIT - 1] + "…"
