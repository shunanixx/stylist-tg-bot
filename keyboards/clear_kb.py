from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

CLEAR_CONFIRM = "clear:yes"
CLEAR_CANCEL = "clear:no"


def clear_confirm_kb() -> InlineKeyboardMarkup:
    """Удаление сообщений необратимо, поэтому спрашиваем до, а не после."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🧹 Очистить", callback_data=CLEAR_CONFIRM),
        InlineKeyboardButton(text="Отмена", callback_data=CLEAR_CANCEL),
    )
    return builder.as_markup()
