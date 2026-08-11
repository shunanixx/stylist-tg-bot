"""Шифрование пользовательских API-ключей.

Ключи чужие: в БД они лежат зашифрованными, чтобы утечка файла базы
(бэкап, синхронизация, доступ к диску) не раздавала доступ к чужим квотам.

Мастер-ключ берётся из ENCRYPTION_KEY, а если его нет — детерминированно
выводится из токена бота. Токен всё равно самый секретный элемент .env,
и так оператору не нужно заводить ещё один секрет. Обратная сторона: смена
токена бота делает сохранённые ключи нечитаемыми, и их придётся ввести заново.
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_DERIVATION_SALT = "stylist-bot:api-key-encryption:v1"


def derive_key(secret: str) -> str:
    """Fernet требует 32 байта в urlsafe-base64 — приводим любой секрет к нему."""
    digest = hashlib.sha256(f"{_DERIVATION_SALT}:{secret}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()


class KeyVault:
    def __init__(self, secret: str) -> None:
        self._fernet = Fernet(derive_key(secret))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str | None) -> str | None:
        """None вместо исключения: сменился мастер-ключ — пользователь просто
        вводит API-ключ заново, бот не должен падать на каждом его сообщении."""
        if not token:
            return None
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            logger.warning("Не удалось расшифровать сохранённый ключ — нужен повторный ввод")
            return None


def mask(api_key: str) -> str:
    """Для показа пользователю: сам ключ в чат больше не возвращаем."""
    if len(api_key) <= 8:
        return "•" * len(api_key)
    return f"{api_key[:4]}…{api_key[-4:]}"
