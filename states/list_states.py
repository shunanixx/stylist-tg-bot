from aiogram.fsm.state import State, StatesGroup


class StyleInput(StatesGroup):
    """Ввод после нажатия кнопки: пользователь присылает обычный текст, без команд."""

    name = State()
    # Переименование: id стиля лежит в данных FSM — кнопку с ним уже нажали
    new_name = State()


class WardrobeInput(StatesGroup):
    title = State()


class WishlistInput(StatesGroup):
    title = State()
