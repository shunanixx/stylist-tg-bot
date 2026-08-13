"""Перехват исходящих сообщений на уровне API-запросов.

Ловим тут, а не в хендлерах: message.answer(...) разбросан по всему проекту,
и вспоминать про учёт в каждом вызове — верный способ забыть в половине мест.
"""

import logging

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Message

from services.chat_tracker import record

logger = logging.getLogger(__name__)


class TrackSentMessagesMiddleware(BaseRequestMiddleware):
    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> TelegramType:
        result = await make_request(bot, method)

        # `make_request` отдаёт уже развёрнутый результат, а не Response: искать
        # в нём `.result` — значит не записать ни одного id, и /clear молча
        # оставит в чате все ответы бота.
        # sendMediaGroup возвращает список, остальные send* — одно сообщение
        for item in result if isinstance(result, list) else [result]:
            if isinstance(item, Message):
                record(item.chat.id, item.message_id)

        return result
