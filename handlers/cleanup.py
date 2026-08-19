"""Очистка чата от переписки. Данные остаются в БД.

Разделение простое: из чата уходят сообщения, из бота — ничего. Разборы
читаются через /history, вещи — через /wardrobe и /wishlist. Полное удаление
данных — это /forget, отдельная команда с отдельным подтверждением.
"""

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import chat_log as chat_log_crud
from keyboards.clear_kb import CLEAR_CANCEL, CLEAR_CONFIRM, clear_confirm_kb

logger = logging.getLogger(__name__)
router = Router(name="cleanup")

# Лимит Telegram на deleteMessages
BATCH = 100

NOTHING_TO_CLEAR = (
    "Чистить нечего — свежих сообщений в журнале нет.\n\n"
    "Бот помнит id сообщений только за последние 48 часов: старше Telegram "
    "удалять не разрешает."
)


@router.message(Command("clear"))
async def cmd_clear(
    message: Message, session: AsyncSession, state: FSMContext | None = None
) -> None:
    # Команда посреди чужого FSM-диалога обязана прервать его — иначе
    # следующий текст пользователя уйдёт в тот незакрытый ввод.
    if state is not None and await state.get_state() is not None:
        await state.clear()
        await message.answer("Ввод прерван — вернулся в меню.")
    tracked = await chat_log_crud.count_tracked(
        session, message.from_user.id, message.chat.id
    )
    if not tracked:
        await message.answer(NOTHING_TO_CLEAR)
        return

    await message.answer(
        f"Удалить из чата {tracked} сообщ. — переписку, разборы, списки?\n\n"
        "Данные останутся: разборы в /history, вещи в /wardrobe и /wishlist, "
        "стили в /styles, замеры в /profile. Из чата уйдёт только текст.",
        reply_markup=clear_confirm_kb(),
    )


@router.callback_query(F.data == CLEAR_CANCEL)
async def cancel_clear(callback: CallbackQuery) -> None:
    await callback.answer("Отменил")
    await callback.message.edit_text("Отменил, чат оставил как есть.")


@router.callback_query(F.data == CLEAR_CONFIRM)
async def confirm_clear(
    callback: CallbackQuery, session: AsyncSession, bot: Bot
) -> None:
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    await callback.answer("Чищу…")

    ids = await chat_log_crud.list_ids(session, user_id, chat_id)
    # Сообщение с кнопками удаляем в самом конце: до этого оно единственный
    # признак, что бот занят
    keep_until_last = callback.message.message_id
    targets = [message_id for message_id in ids if message_id != keep_until_last]

    deleted, failed = await delete_messages(bot, chat_id, targets)

    # Забываем только реально удалённое: если сообщение не поддалось из-за
    # 48 часов, повторная попытка всё равно не сработает — но и висеть в
    # журнале ему незачем
    await chat_log_crud.forget_all(session, user_id, chat_id)

    report = f"🧹 Убрал {deleted} сообщ."
    if failed:
        report += (
            f"\n{failed} не поддались — Telegram не даёт боту удалять сообщения "
            "старше 48 часов. Их можно убрать вручную."
        )
    report += "\n\nДанные целы: /history, /wardrobe, /wishlist, /styles, /profile."

    await bot.send_message(chat_id, report)
    await _delete_one(bot, chat_id, keep_until_last)


async def delete_messages(
    bot: Bot, chat_id: int, message_ids: list[int]
) -> tuple[int, int]:
    """Удаляет пачками по 100. Возвращает (удалено, не удалось).

    Пачка падает целиком, если хоть одно сообщение старше 48 часов, поэтому
    на ошибке добиваем поштучно — иначе одно старое сообщение утащило бы
    за собой 99 удаляемых.
    """
    deleted = 0
    failed = 0
    for start in range(0, len(message_ids), BATCH):
        batch = message_ids[start : start + BATCH]
        try:
            await bot.delete_messages(chat_id=chat_id, message_ids=batch)
            deleted += len(batch)
        except TelegramAPIError:
            ok, bad = await _delete_one_by_one(bot, chat_id, batch)
            deleted += ok
            failed += bad
    return deleted, failed


async def _delete_one_by_one(
    bot: Bot, chat_id: int, message_ids: list[int]
) -> tuple[int, int]:
    deleted = 0
    failed = 0
    for message_id in message_ids:
        if await _delete_one(bot, chat_id, message_id):
            deleted += 1
        else:
            failed += 1
    return deleted, failed


async def _delete_one(bot: Bot, chat_id: int, message_id: int) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except TelegramAPIError:
        return False
