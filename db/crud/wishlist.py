from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import WishlistItem


async def list_items(
    session: AsyncSession, user_id: int, active_only: bool = True
) -> list[WishlistItem]:
    query = select(WishlistItem).where(WishlistItem.user_id == user_id)
    if active_only:
        query = query.where(WishlistItem.active.is_(True))
    result = await session.scalars(query.order_by(WishlistItem.added_at, WishlistItem.id))
    return list(result)


async def add_item(
    session: AsyncSession,
    user_id: int,
    title: str,
    category: str | None = None,
    note: str | None = None,
    verdict: str | None = None,
    source_submission_id: int | None = None,
) -> WishlistItem:
    item = WishlistItem(
        user_id=user_id,
        title=title,
        category=category,
        note=note,
        verdict=verdict,
        source_submission_id=source_submission_id,
    )
    session.add(item)
    await session.flush()
    return item


async def deactivate_item(
    session: AsyncSession, user_id: int, item_id: int
) -> WishlistItem | None:
    """Soft delete — как в гардеробе, запись остаётся для истории."""
    item = await session.get(WishlistItem, item_id)
    if item is None or item.user_id != user_id:
        return None
    item.active = False
    await session.flush()
    return item


async def get_item(
    session: AsyncSession, user_id: int, item_id: int
) -> WishlistItem | None:
    item = await session.get(WishlistItem, item_id)
    if item is None or item.user_id != user_id or not item.active:
        return None
    return item


async def find_by_submission(
    session: AsyncSession, user_id: int, submission_id: int
) -> WishlistItem | None:
    """Защита от повторного добавления одного разбора."""
    result = await session.scalars(
        select(WishlistItem).where(
            WishlistItem.user_id == user_id,
            WishlistItem.source_submission_id == submission_id,
            WishlistItem.active.is_(True),
        )
    )
    return result.first()


async def count_items(session: AsyncSession, user_id: int) -> int:
    return len(await list_items(session, user_id))
