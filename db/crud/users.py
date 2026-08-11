from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User

MEASUREMENT_FIELDS = (
    "height_cm",
    "weight_kg",
    "shoulders_cm",
    "chest_cm",
    "waist_cm",
    "belt_cm",
)


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_or_create_user(
    session: AsyncSession, user_id: int, default_provider: str = "gemini"
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(user_id=user_id, default_llm_provider=default_provider)
        session.add(user)
        await session.flush()
    return user


async def update_measurements(
    session: AsyncSession, user_id: int, **values: float | None
) -> User:
    unknown = set(values) - set(MEASUREMENT_FIELDS)
    if unknown:
        raise ValueError(f"Неизвестные поля параметров: {sorted(unknown)}")
    user = await get_or_create_user(session, user_id)
    for field, value in values.items():
        setattr(user, field, value)
    await session.flush()
    return user


async def set_onboarded(session: AsyncSession, user_id: int, value: bool = True) -> User:
    user = await get_or_create_user(session, user_id)
    user.onboarded = value
    await session.flush()
    return user


async def set_default_provider(
    session: AsyncSession, user_id: int, provider: str
) -> User:
    user = await get_or_create_user(session, user_id)
    user.default_llm_provider = provider
    await session.flush()
    return user


async def set_api_key(session: AsyncSession, user_id: int, encrypted: str | None) -> User:
    """Принимает уже зашифрованное значение: открытый ключ в CRUD не попадает."""
    user = await get_or_create_user(session, user_id)
    user.google_api_key_enc = encrypted
    await session.flush()
    return user


async def count_users(session: AsyncSession) -> int:
    result = await session.scalar(select(func.count()).select_from(User))
    return int(result or 0)


async def count_users_with_key(session: AsyncSession) -> int:
    result = await session.scalar(
        select(func.count()).select_from(User).where(User.google_api_key_enc.is_not(None))
    )
    return int(result or 0)


async def list_users(session: AsyncSession) -> list[User]:
    result = await session.scalars(select(User).order_by(User.created_at))
    return list(result)


async def delete_user(session: AsyncSession, user_id: int) -> bool:
    """Полное удаление вместе с гардеробом, вишлистом и разборами (cascade)."""
    user = await session.get(User, user_id)
    if user is None:
        return False
    await session.delete(user)
    await session.flush()
    return True
