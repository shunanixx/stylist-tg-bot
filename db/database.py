from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base


class Database:
    """Обёртка над async-движком SQLAlchemy.

    Схема создаётся через create_all. Alembic подключается на этапе 6, когда
    структура начнёт меняться на реальных данных.
    """

    def __init__(self, url: str, echo: bool = False) -> None:
        self._engine = create_async_engine(url, echo=echo)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )

    async def create_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self._engine.dispose()
