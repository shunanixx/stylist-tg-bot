"""/model: показывает провайдера/выбор и не должно тихо ломать чужой FSM-ввод."""

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from db.database import Database
from handlers.model_selection import cmd_model
from states.list_states import WardrobeInput

USER_ID = 3030


class FakeMessage:
    def __init__(self, user_id: int = USER_ID):
        self.from_user = type("U", (), {"id": user_id})()
        self.sent: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.sent.append(text)


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


@pytest.fixture
def state():
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=USER_ID, user_id=USER_ID),
    )


async def test_single_provider_shows_status_without_a_keyboard(session):
    message = FakeMessage()

    await cmd_model(message, session)

    assert "gemini" in message.sent[-1]


async def test_model_mid_wardrobe_add_clears_the_pending_state(session, state):
    """Раньше /model посреди «жду название вещи в /wardrobe» не трогал FSM —
    и следующее обычное сообщение пользователя уходило в БД как название вещи."""
    await state.set_state(WardrobeInput.title)
    message = FakeMessage()

    await cmd_model(message, session, state)

    assert await state.get_state() is None
    assert "Ввод прерван" in message.sent[0]


async def test_model_without_pending_state_does_not_mention_interruption(
    session, state
):
    message = FakeMessage()

    await cmd_model(message, session, state)

    assert not any("Ввод прерван" in text for text in message.sent)
