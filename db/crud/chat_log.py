from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ChatMessage

# Telegram разрешает боту удалять свои сообщения не старше 48 часов, чужие —
# только в группах с правами админа. Всё, что старше, удалить уже нельзя,
# поэтому записи не копим бесконечно.
RETENTION_HOURS = 48


async def remember_many(
    session: AsyncSession, user_id: int, entries: list[tuple[int, int]]
) -> None:
    """entries — пары (chat_id, message_id). Дубли отсекаем: один и тот же
    message_id мог прийти и из буфера исходящих, и из входящего события."""
    for chat_id, message_id in dict.fromkeys(entries):
        session.add(
            ChatMessage(user_id=user_id, chat_id=chat_id, message_id=message_id)
        )
    await session.flush()


async def list_ids(session: AsyncSession, user_id: int, chat_id: int) -> list[int]:
    """Свежие сначала: если упрёмся в лимит Telegram, уберём хотя бы верх чата."""
    result = await session.scalars(
        select(ChatMessage.message_id)
        .where(ChatMessage.user_id == user_id, ChatMessage.chat_id == chat_id)
        .distinct()
        .order_by(ChatMessage.message_id.desc())
    )
    return list(result)


async def forget(
    session: AsyncSession, user_id: int, chat_id: int, message_ids: list[int]
) -> None:
    """Убирает записи о том, что уже удалено из чата."""
    if not message_ids:
        return
    await session.execute(
        delete(ChatMessage).where(
            ChatMessage.user_id == user_id,
            ChatMessage.chat_id == chat_id,
            ChatMessage.message_id.in_(message_ids),
        )
    )
    await session.flush()


async def forget_all(session: AsyncSession, user_id: int, chat_id: int) -> None:
    await session.execute(
        delete(ChatMessage).where(
            ChatMessage.user_id == user_id, ChatMessage.chat_id == chat_id
        )
    )
    await session.flush()


async def count_tracked(session: AsyncSession, user_id: int, chat_id: int) -> int:
    return len(await list_ids(session, user_id, chat_id))
