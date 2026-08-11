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
from aiogram.methods.base import Response, TelegramType
from aiogram.types import Message

from services.chat_tracker import record

logger = logging.getLogger(__name__)


class TrackSentMessagesMiddleware(BaseRequestMiddleware):
    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        response = await make_request(bot, method)

        result = getattr(response, "result", None)
        # sendMediaGroup возвращает список, остальные send* — одно сообщение
        for item in result if isinstance(result, list) else [result]:
            if isinstance(item, Message):
                record(item.chat.id, item.message_id)

        return response
