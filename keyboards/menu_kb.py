from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# Reply-кнопка присылает обычный текст, callback_data у неё нет — опознать
# нажатие можно только по подписи. Отсюда таблица «подпись → раздел»: она же
# фильтр хендлера, она же раскладка клавиатуры, в одном месте.
MENU_BUTTONS: tuple[tuple[str, str], ...] = (
    ("🎨 Мои стили", "styles"),
    ("📐 Параметры", "profile"),
    ("🧥 Гардероб", "wardrobe"),
    ("⭐ Вишлист", "wishlist"),
    ("📜 Разборы", "history"),
    ("🔑 Ключ", "apikey"),
    ("❓ Помощь", "help"),
)

BUTTON_TO_ACTION: dict[str, str] = {text: action for text, action in MENU_BUTTONS}
BUTTON_TEXTS: frozenset[str] = frozenset(BUTTON_TO_ACTION)


def main_menu_kb() -> ReplyKeyboardMarkup:
    """Постоянное меню под полем ввода. Команды никуда не деваются — кнопка
    ведёт в тот же хендлер, что и слеш-команда."""
    builder = ReplyKeyboardBuilder()
    for text, _ in MENU_BUTTONS:
        builder.button(text=text)
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Описание вещи или фото",
    )
