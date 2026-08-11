from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WardrobeItem


async def list_items(
    session: AsyncSession, user_id: int, active_only: bool = True
) -> list[WardrobeItem]:
    query = select(WardrobeItem).where(WardrobeItem.user_id == user_id)
    if active_only:
        query = query.where(WardrobeItem.active.is_(True))
    result = await session.scalars(query.order_by(WardrobeItem.added_at, WardrobeItem.id))
    return list(result)


async def add_item(
    session: AsyncSession,
    user_id: int,
    title: str,
    category: str | None = None,
    color: str | None = None,
    size: str | None = None,
    source_submission_id: int | None = None,
) -> WardrobeItem:
    item = WardrobeItem(
        user_id=user_id,
        title=title,
        category=category,
        color=color,
        size=size,
        source_submission_id=source_submission_id,
    )
    session.add(item)
    await session.flush()
    return item


async def deactivate_item(
    session: AsyncSession, user_id: int, item_id: int
) -> WardrobeItem | None:
    """Soft delete — запись остаётся для истории анализов."""
    item = await session.get(WardrobeItem, item_id)
    if item is None or item.user_id != user_id:
        return None
    item.active = False
    await session.flush()
    return item


async def count_items(session: AsyncSession, user_id: int) -> int:
    items = await list_items(session, user_id)
    return len(items)
