"""Общая механика инлайн-управления списками.

Все три раздела (стили, гардероб, вишлист) устроены одинаково: под списком —
кнопки, нажатие либо просит текст, либо предлагает выбрать вещь кнопкой.
Разница только в CRUD, поэтому здесь лежит именно механика, а не разделы.
"""

import logging
from collections.abc import Callable, Sequence
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import CallbackQuery

from keyboards import list_kb

logger = logging.getLogger(__name__)


async def drop_keyboard(callback: CallbackQuery) -> None:
    """Снимает кнопки с нажатого сообщения: под ними уже прошлый список, и
    второе нажатие ушло бы по устаревшему id."""
    message = getattr(callback, "message", None)
    if message is None:
        return
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        # Сообщение старше 48 часов или клавиатуру уже сняли — не повод падать
        pass


async def ask_text(
    callback: CallbackQuery,
    state: FSMContext,
    section: str,
    next_state: State,
    text: str,
    **data: Any,
) -> None:
    """Просит ввод обычным текстом: команду набирать не нужно, выход — кнопкой."""
    await drop_keyboard(callback)
    await state.set_state(next_state)
    if data:
        await state.update_data(**data)
    if callback.message is not None:
        await callback.message.answer(text, reply_markup=list_kb.cancel_kb(section))
    await callback.answer()


async def ask_pick(
    callback: CallbackQuery,
    section: str,
    action: str,
    items: Sequence[Any],
    label: Callable[[Any], str],
    text: str,
    empty_text: str,
) -> None:
    """Выбор вещи кнопкой — вместо «/remove 3» с номером, списанным из списка."""
    if not items:
        await callback.answer(empty_text, show_alert=True)
        return
    await drop_keyboard(callback)
    if callback.message is not None:
        tail = (
            ""
            if len(items) <= list_kb.PICK_LIMIT
            else f"\nПоказал первые {list_kb.PICK_LIMIT} — остальное командой."
        )
        await callback.message.answer(
            text + tail,
            reply_markup=list_kb.pick_kb(section, action, items, label),
        )
    await callback.answer()
