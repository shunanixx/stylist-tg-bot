"""Гардероб: нумерация по позиции, а не по id из БД."""

import pytest
import pytest_asyncio

from db.crud import wardrobe as wardrobe_crud
from db.database import Database
from handlers.wardrobe import cmd_add, cmd_remove, cmd_wardrobe

USER_ID = 321
OTHER_USER_ID = 322


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


async def _fill(session, *titles):
    for title in titles:
        await wardrobe_crud.add_item(session, USER_ID, title)


async def test_listing_numbers_from_one(session):
    await _fill(session, "Свитшот", "Джинсы")

    message = FakeMessage()
    await cmd_wardrobe(message, session)

    assert "1. Свитшот" in message.sent[0]
    assert "2. Джинсы" in message.sent[0]


async def test_numbering_has_no_holes_after_removal(session):
    """Раньше показывался item.id, и после удаления оставались дыры: 1, 3."""
    await _fill(session, "Первая", "Вторая", "Третья")

    await cmd_remove(FakeMessage(), FakeCommand("2"), session)

    listing = FakeMessage()
    await cmd_wardrobe(listing, session)
    assert "1. Первая" in listing.sent[0]
    assert "2. Третья" in listing.sent[0]
    assert "3." not in listing.sent[0]


async def test_new_item_continues_list_numbering(session):
    """Новая вещь получает следующий номер по списку, а не следующий id."""
    await _fill(session, "Первая", "Вторая", "Третья")
    await cmd_remove(FakeMessage(), FakeCommand("1"), session)

    added = FakeMessage()
    await cmd_add(added, FakeCommand("Четвёртая"), session)

    assert "№3" in added.sent[0]


async def test_number_addresses_what_user_sees(session):
    """После сдвига номеров команда должна попадать в показанную вещь."""
    await _fill(session, "Первая", "Вторая", "Третья")
    await cmd_remove(FakeMessage(), FakeCommand("1"), session)

    removed = FakeMessage()
    await cmd_remove(removed, FakeCommand("1"), session)

    assert "Вторая" in removed.sent[0]
    assert [i.title for i in await wardrobe_crud.list_items(session, USER_ID)] == ["Третья"]


@pytest.mark.parametrize("args", [None, "", "не число", "0", "-1", "99"])
async def test_bad_number_removes_nothing(session, args):
    await _fill(session, "Свитшот")

    message = FakeMessage()
    await cmd_remove(message, FakeCommand(args), session)

    assert len(await wardrobe_crud.list_items(session, USER_ID)) == 1
    assert message.sent


async def test_number_cannot_reach_another_users_item(session):
    await wardrobe_crud.add_item(session, OTHER_USER_ID, "Чужая вещь")

    message = FakeMessage()
    await cmd_remove(message, FakeCommand("1"), session)

    assert len(await wardrobe_crud.list_items(session, OTHER_USER_ID)) == 1
    assert "пуст" in message.sent[0].lower()
