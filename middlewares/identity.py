"""Запоминание @username и имени.

Telegram присылает их с каждым апдейтом, но нигде не хранит за нас: в БД
лежит только `user_id`, и по нему потом не понять, кто это. `/numbers`
показывает именно эти поля, поэтому собираем их со всех апдейтов, а не
только с /start — большинство пользователей заходит один раз и дальше
жмёт кнопки.

Стоит после DbSessionMiddleware: пишем в ту же сессию, что и хендлер.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from db.crud import users as users_crud

logger = logging.getLogger(__name__)


class IdentityMiddleware(BaseMiddleware):
    def __init__(self, default_provider: str = "gemini") -> None:
        # Строка пользователя может быть создана здесь, до /start: провайдер
        # по умолчанию берём из конфига, а не из дефолта модели.
        self.default_provider = default_provider

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        user = data.get("event_from_user")
        if session is not None and user is not None:
            try:
                await users_crud.remember_identity(
                    session,
                    user.id,
                    user.username,
                    user.first_name,
                    self.default_provider,
                )
            except Exception:
                # Вспомогательная запись: ответ пользователю из-за неё не роняем
                logger.exception("Не удалось запомнить username")
                # Откат обязателен: упавший запрос оставляет транзакцию
                # сломанной, и дальше в хендлере падало бы уже всё — так
                # отсутствие одной колонки в БД убивало половину бота.
                try:
                    await session.rollback()
                except Exception:
                    logger.exception("Не удалось откатить транзакцию")

        return await handler(event, data)
