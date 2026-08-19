"""История разборов: нумерация по позиции, свежие сверху."""

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from db.crud import submissions as submissions_crud
from db.database import Database
from handlers.history import cmd_history, cmd_show
from states.list_states import WardrobeInput

USER_ID = 6161
OTHER_USER_ID = 6162


class FakeMessage:
    def __init__(self, user_id: int = USER_ID):
        self.from_user = type("U", (), {"id": user_id})()
        self.sent: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.sent.append(text)


class FakeCommand:
    def __init__(self, args: str | None):
        self.args = args


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


async def _analyzed(session, title, verdict="брать", user_id=USER_ID):
    """Разбор с ответом модели — как его создаёт хендлер анализа."""
    submission = await submissions_crud.create_submission(
        session, user_id, "text", title
    )
    await submissions_crud.add_result(
        session,
        submission_id=submission.id,
        provider="gemini",
        verdict=verdict,
        full_response=f"Разбор: {title}",
        raw_response=f"Разбор: {title}",
    )
    await submissions_crud.set_item_meta(session, submission.id, USER_ID, title, "верх", verdict)
    return submission


async def test_numbers_start_from_one(session):
    await _analyzed(session, "Куртка Carhartt")
    await _analyzed(session, "Кеды Puma")

    message = FakeMessage()
    await cmd_history(message, session)

    assert "1. " in message.sent[0]
    assert "2. " in message.sent[0]


async def test_newest_analysis_is_first(session):
    """Свежий разбор нужен сверху — за ним и приходят чаще всего."""
    await _analyzed(session, "Старая куртка")
    await _analyzed(session, "Новые кеды")

    message = FakeMessage()
    await cmd_history(message, session)

    lines = message.sent[0].splitlines()
    first = next(line for line in lines if line.strip().startswith(("✅", "❌", "•")))
    assert "Новые кеды" in first


async def test_database_id_is_not_shown(session):
    """Раньше печатался submission.id — сквозной по всей таблице."""
    for index in range(5):
        await _analyzed(session, f"Вещь {index}")

    message = FakeMessage()
    await cmd_history(message, session)

    # id пятого разбора — 5, но это не должно быть его номером в списке
    assert "5. Вещь 4" not in message.sent[0]
    assert "1. " in message.sent[0]


async def test_show_number_matches_the_listing(session):
    """Номер из списка обязан открывать именно ту вещь, что показана."""
    await _analyzed(session, "Куртка Carhartt")
    await _analyzed(session, "Кеды Puma")

    message = FakeMessage()
    await cmd_show(message, FakeCommand("1"), session)

    assert "Кеды Puma" in message.sent[0]


async def test_show_second_position(session):
    await _analyzed(session, "Куртка Carhartt")
    await _analyzed(session, "Кеды Puma")

    message = FakeMessage()
    await cmd_show(message, FakeCommand("2"), session)

    assert "Куртка Carhartt" in message.sent[0]


async def test_unanalyzed_submissions_do_not_shift_numbers(session):
    """Разборы без названия в список не попадают — и не должны сдвигать нумерацию."""
    await _analyzed(session, "Куртка Carhartt")
    # упавший разбор: запись есть, item_title пуст
    await submissions_crud.create_submission(session, USER_ID, "text", "не разобралось")
    await _analyzed(session, "Кеды Puma")

    listing = FakeMessage()
    await cmd_history(listing, session)
    assert "1. " in listing.sent[0] and "2. " in listing.sent[0]
    assert "3. " not in listing.sent[0]

    shown = FakeMessage()
    await cmd_show(shown, FakeCommand("1"), session)
    assert "Кеды Puma" in shown.sent[0]


@pytest.mark.parametrize("args", [None, "", "не число", "0", "-1", "99"])
async def test_show_with_bad_number_explains(session, args):
    await _analyzed(session, "Куртка Carhartt")

    message = FakeMessage()
    await cmd_show(message, FakeCommand(args), session)

    assert "номер" in message.sent[0].lower()


async def test_number_cannot_reach_another_users_analysis(session):
    await _analyzed(session, "Чужая куртка", user_id=OTHER_USER_ID)

    message = FakeMessage()
    await cmd_show(message, FakeCommand("1"), session)

    assert "Чужая куртка" not in message.sent[0]
    assert "нет" in message.sent[0].lower()


async def test_empty_history_asks_for_an_item(session):
    message = FakeMessage()

    await cmd_history(message, session)

    assert "Разборов пока нет" in message.sent[0]


async def test_history_mid_wardrobe_add_clears_the_pending_state(session):
    """Раньше /history посреди «жду название вещи в /wardrobe» не трогал
    FSM — и следующее обычное сообщение уходило в БД как название вещи."""
    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=USER_ID, user_id=USER_ID),
    )
    await state.set_state(WardrobeInput.title)
    message = FakeMessage()

    await cmd_history(message, session, state)

    assert await state.get_state() is None
    assert "Ввод прерван" in message.sent[0]


async def test_show_mid_wardrobe_add_clears_the_pending_state(session):
    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=USER_ID, user_id=USER_ID),
    )
    await state.set_state(WardrobeInput.title)
    message = FakeMessage()

    await cmd_show(message, FakeCommand(""), session, state)

    assert await state.get_state() is None
