from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

ADD_TO_WARDROBE_PREFIX = "wardrobe:add"
ADD_TO_WISHLIST_PREFIX = "wishlist:add"


def analysis_actions_kb(submission_id: int) -> InlineKeyboardMarkup:
    """Гардероб — вещь уже моя, вишлист — присматриваю. Оба варианта сразу:
    решение принимается ровно здесь, сразу после разбора."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="➕ В гардероб",
            callback_data=f"{ADD_TO_WARDROBE_PREFIX}:{submission_id}",
        ),
        InlineKeyboardButton(
            text="⭐ В вишлист",
            callback_data=f"{ADD_TO_WISHLIST_PREFIX}:{submission_id}",
        ),
    )
    return builder.as_markup()
