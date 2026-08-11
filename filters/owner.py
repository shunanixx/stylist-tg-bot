from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject


class IsOwner(BaseFilter):
    """Точечная проверка на конкретных командах вместо шлюза на весь бот:
    бот публичный, приватны только админские команды."""

    def __init__(self, owner_user_id: int) -> None:
        self.owner_user_id = owner_user_id

    async def __call__(self, event: TelegramObject) -> bool:
        if not self.owner_user_id:
            return False
        user = getattr(event, "from_user", None)
        return user is not None and user.id == self.owner_user_id
