"""Кто каким ключом ходит в модель.

Бот публичный: у каждого пользователя свой ключ Gemini, его квота тратится
только на его разборы. Владелец — исключение: у него ключ уже лежит в .env,
второй раз вводить его через /apikey не нужно.
"""

from dataclasses import dataclass

from config import Settings
from db.models import User
from services.crypto import KeyVault
from services.limits import is_owner


@dataclass(frozen=True)
class KeySource:
    api_key: str | None
    is_own: bool  # False — ключ владельца из .env

    @property
    def present(self) -> bool:
        return bool(self.api_key)


def resolve_api_key(user: User, vault: KeyVault, cfg: Settings) -> KeySource:
    own = vault.decrypt(user.google_api_key_enc)
    if own:
        return KeySource(api_key=own, is_own=True)

    # Проверка владельца одна на весь код — services.limits
    if is_owner(user.user_id, cfg) and cfg.google_api_key:
        return KeySource(api_key=cfg.google_api_key, is_own=False)

    return KeySource(api_key=None, is_own=False)


NO_KEY_MESSAGE = (
    "🔑 Нужен твой ключ Gemini — бот работает на ключе пользователя, "
    "чтобы твои разборы не упирались в чужой лимит.\n\n"
    "<b>Где взять (1 минута, бесплатно)</b>\n"
    "1. Открой <a href=\"https://aistudio.google.com/apikey\">aistudio.google.com/apikey</a>\n"
    "2. «Create API key» → скопируй строку\n"
    "3. Пришли сюда: <code>/apikey ВСТАВЬ_КЛЮЧ</code>\n\n"
    "Сообщение с ключом я сразу удалю, а сам ключ сохраню в зашифрованном виде. "
    "Убрать его потом — /apikey_off."
)
