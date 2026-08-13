"""Запись id сообщений в журнал для /clear.

Стоит после DbSessionMiddleware: пишем в ту же сессию, что и хендлер, поэтому
запись идёт одной транзакцией и не конкурирует с ней за блокировку SQLite.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update

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
        incoming = _incoming_message(event)
        if incoming is not None:
            entries.append((incoming.chat.id, incoming.message_id))

        if not entries:
            return result

        try:
            # Строка пользователя должна уже существовать: её создаёт
            # IdentityMiddleware до хендлера. Если её нет — значит хендлер
            # был /forget: воскрешать удалённого нельзя, журнал не нужен.
            if await users_crud.get_user(session, user.id) is None:
                return result
            await chat_log_crud.remember_many(session, user.id, entries)
        except Exception:
            # Учёт сообщений — вспомогательная вещь: не роняем ответ из-за него
            logger.exception("Не удалось записать сообщения в журнал чата")
            # Упавший flush ломает транзакцию: без отката на ней падал бы
            # коммит уже за пределами middleware, в разборе апдейта.
            try:
                await session.rollback()
            except Exception:
                logger.exception("Не удалось откатить транзакцию журнала")

        return result


def _incoming_message(event: TelegramObject) -> Message | None:
    """Апдейт целиком, а не Message: middleware висит на `dispatcher.update`.

    Раньше здесь стоял `isinstance(event, Message)` — на Update он никогда не
    срабатывал, и в журнал не попадало ни одно сообщение пользователя.
    """
    if isinstance(event, Update):
        event = event.message or event.edited_message
    return event if isinstance(event, Message) else None
