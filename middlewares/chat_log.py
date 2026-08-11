"""Запись id сообщений в журнал для /clear.

Стоит после DbSessionMiddleware: пишем в ту же сессию, что и хендлер, поэтому
запись идёт одной транзакцией и не конкурирует с ней за блокировку SQLite.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from db.crud import chat_log as chat_log_crud
from db.crud import users as users_crud
from services.chat_tracker import start_recording, stop_recording

logger = logging.getLogger(__name__)


class ChatLogMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        outgoing = start_recording()
        try:
            result = await handler(event, data)
        finally:
            stop_recording()

        session = data.get("session")
        user = data.get("event_from_user")
        if session is None or user is None:
            return result

        entries = list(outgoing)
        # Своё сообщение пользователя — тоже мусор в чате, его учитываем.
        # У callback_query сообщение чужое (наше собственное, уже записанное
        # при отправке) — второй раз не пишем.
        if isinstance(event, Message):
            entries.append((event.chat.id, event.message_id))

        if not entries:
            return result

        try:
            # get_or_create: chat_messages ссылается на users, а первым
            # апдейтом может быть что угодно, не обязательно /start
            await users_crud.get_or_create_user(session, user.id)
            await chat_log_crud.remember_many(session, user.id, entries)
        except Exception:
            # Учёт сообщений — вспомогательная вещь: не роняем ответ из-за него
            logger.exception("Не удалось записать сообщения в журнал чата")

        return result
