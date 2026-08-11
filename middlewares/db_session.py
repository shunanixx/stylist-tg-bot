from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from db.database import Database


class DbSessionMiddleware(BaseMiddleware):
    """Кладёт AsyncSession в data['session'] — коммит на выходе из хендлера."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self.database.session() as session:
            data["session"] = session
            return await handler(event, data)
