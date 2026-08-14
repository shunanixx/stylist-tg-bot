from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base


def engine_options(url: str) -> dict[str, object]:
    """Пул под serverless-Postgres (Neon, Supabase) — без пула.

    Бесплатный Neon засыпает через 5 минут без запросов и тарифицирует
    CU-часы, пока не спит. Обычный пул держит соединение открытым сутками,
    то есть база не засыпает никогда и месячная квота сгорает за две недели.
    NullPool закрывает соединение сразу после сессии. Плата за это —
    коннект (~30 мс) на каждый апдейт, что рядом с 13 секундами разбора
    у модели незаметно.

    SQLite остаётся на пуле по умолчанию: файл локальный, и переоткрывать
    его на каждый запрос — только лишние блокировки.
    """
    if url.startswith("postgres"):
        return {"poolclass": NullPool}
    return {}


class Database:
    """Обёртка над async-движком SQLAlchemy.

    Схема создаётся через create_all. Alembic подключается на этапе 6, когда
    структура начнёт меняться на реальных данных.
    """

    def __init__(self, url: str, echo: bool = False) -> None:
        self._engine = create_async_engine(url, echo=echo, **engine_options(url))
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
