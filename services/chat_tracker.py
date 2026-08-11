"""Учёт id сообщений для команды /clear.

Telegram не даёт боту читать историю чата: удалить можно только то, чей
message_id известен. Поэтому id запоминаются по ходу переписки.

Исходящие собираются в contextvar, а не пишутся в БД сразу: транзакция
хендлера в этот момент открыта, и параллельная запись во вторую сессию
упёрлась бы в блокировку SQLite. Буфер сбрасывается в ту же сессию после
хендлера, одним пакетом.
"""

from contextvars import ContextVar

# (chat_id, message_id) сообщений, отправленных при обработке одного апдейта
_outgoing: ContextVar[list[tuple[int, int]] | None] = ContextVar(
    "outgoing_messages", default=None
)


def start_recording() -> list[tuple[int, int]]:
    """Новый буфер на апдейт. Задача aiogram копирует контекст, поэтому
    буферы разных пользователей не пересекаются."""
    buffer: list[tuple[int, int]] = []
    _outgoing.set(buffer)
    return buffer


def record(chat_id: int, message_id: int) -> None:
    buffer = _outgoing.get()
    if buffer is not None:
        buffer.append((chat_id, message_id))


def stop_recording() -> None:
    _outgoing.set(None)
