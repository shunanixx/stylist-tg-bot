from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import StyleItem

# Стартового набора нет намеренно: чужие стили задают чужую систему координат,
# и разбор в ней бесполезен. Пользователь перечисляет свои сам — после ключа.
MAX_NAME_LENGTH = 60


async def list_styles(session: AsyncSession, user_id: int) -> list[StyleItem]:
    result = await session.scalars(
        select(StyleItem)
        .where(StyleItem.user_id == user_id)
        .order_by(StyleItem.added_at, StyleItem.id)
    )
    return list(result)


async def add_style(session: AsyncSession, user_id: int, name: str) -> StyleItem:
    style = StyleItem(user_id=user_id, name=name.strip())
    session.add(style)
    await session.flush()
    return style


async def find_by_name(
    session: AsyncSession, user_id: int, name: str
) -> StyleItem | None:
    """Сравнение без учёта регистра: «Y2K» и «y2k» — один стиль."""
    target = name.strip().casefold()
    for style in await list_styles(session, user_id):
        if style.name.casefold() == target:
            return style
    return None


async def rename_style(
    session: AsyncSession, user_id: int, style_id: int, new_name: str
) -> StyleItem | None:
    style = await session.get(StyleItem, style_id)
    if style is None or style.user_id != user_id:
        return None
    style.name = new_name.strip()
    await session.flush()
    return style


async def delete_style(
    session: AsyncSession, user_id: int, style_id: int
) -> StyleItem | None:
    style = await session.get(StyleItem, style_id)
    if style is None or style.user_id != user_id:
        return None
    await session.delete(style)
    await session.flush()
    return style


async def count_styles(session: AsyncSession, user_id: int) -> int:
    return len(await list_styles(session, user_id))
