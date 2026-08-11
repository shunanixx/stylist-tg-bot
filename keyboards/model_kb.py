from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

SELECT_MODEL_PREFIX = "model:set"


def model_select_kb(providers: list[str], current: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for name in providers:
        mark = "● " if name == current else "○ "
        builder.row(
            InlineKeyboardButton(
                text=f"{mark}{name}", callback_data=f"{SELECT_MODEL_PREFIX}:{name}"
            )
        )
    return builder.as_markup()
