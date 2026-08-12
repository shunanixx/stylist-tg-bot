import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from handlers import api_key, history, profile, start, styles, wardrobe, wishlist
from keyboards.menu_kb import BUTTON_TEXTS, BUTTON_TO_ACTION, main_menu_kb
from services.crypto import KeyVault

logger = logging.getLogger(__name__)
router = Router(name="menu")

# Кнопка ведёт в тот же хендлер, что и команда: логика раздела не раздваивается
# и расхождений «через кнопку иначе, чем через /wardrobe» не бывает. Подпись
# сигнатур общая, лишние аргументы каждый вызов отбрасывает сам.
_ACTIONS: dict[str, Callable[..., Awaitable[None]]] = {
    "styles": lambda msg, session, state, vault, cfg: styles.cmd_styles(
        msg, session, state
    ),
    "profile": lambda msg, session, state, vault, cfg: profile.cmd_profile(
        msg, session, state
    ),
    "wardrobe": lambda msg, session, state, vault, cfg: wardrobe.cmd_wardrobe(
        msg, session, state
    ),
    "wishlist": lambda msg, session, state, vault, cfg: wishlist.cmd_wishlist(
        msg, session, state
    ),
    "history": lambda msg, session, state, vault, cfg: history.cmd_history(msg, session),
    "apikey": lambda msg, session, state, vault, cfg: api_key.show_key_status(
        msg, session, vault, cfg
    ),
    "help": lambda msg, session, state, vault, cfg: start.cmd_help(msg),
}


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    await message.answer(
        "Меню под полем ввода. Команды работают как раньше — что удобнее.",
        reply_markup=main_menu_kb(),
    )


@router.message(F.text.in_(BUTTON_TEXTS))
async def press_menu_button(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    vault: KeyVault,
    settings: Settings,
) -> None:
    """Роутер стоит до profile и до analysis: иначе подпись кнопки уехала бы
    в незакрытый FSM-диалог как ответ на вопрос о замерах или в модель как
    описание вещи."""
    if await state.get_state() is not None:
        # Нажатие кнопки — явный переход в другой раздел, а не ответ на вопрос
        await state.clear()
        await message.answer("Ввод прерван — вернулся в меню.")

    action = BUTTON_TO_ACTION[message.text]
    await _ACTIONS[action](message, session, state, vault, settings)
