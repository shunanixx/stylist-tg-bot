"""Вишлист: отложенное к покупке, переезд в гардероб, попадание в промпт."""

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramBadRequest

from db.crud import submissions as submissions_crud
from db.crud import wardrobe as wardrobe_crud
from db.crud import wishlist as wishlist_crud
from db.database import Database
from handlers.wishlist import (
    add_from_analysis,
    cmd_bought,
    cmd_wish,
    cmd_wishlist,
    cmd_unwish,
)
from services.prompt_builder import PromptBuilder

USER_ID = 777
OTHER_USER_ID = 778


class FakeMessage:
    def __init__(self, user_id: int = USER_ID):
        self.from_user = type("U", (), {"id": user_id})()
        self.sent: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.sent.append(text)


class FakeCallback:
    def __init__(self, data: str, user_id: int = USER_ID, message_edit_fails: bool = False):
        self.data = data
        self.from_user = type("U", (), {"id": user_id})()
        self.answers: list[str] = []
        self.message = _CallbackMessage(edit_fails=message_edit_fails)

    async def answer(self, text: str = "", show_alert: bool = False, **kwargs):
        self.answers.append(text)


class _CallbackMessage:
    def __init__(self, edit_fails: bool = False):
        self.markup_cleared = False
        self.sent: list[str] = []
        self._edit_fails = edit_fails

    async def edit_reply_markup(self, reply_markup=None, **kwargs):
        if self._edit_fails:
            raise TelegramBadRequest(method=None, message="message to edit not found")
        self.markup_cleared = reply_markup is None

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


async def _submission(session, title="Куртка Carhartt", verdict="брать", category="верхняя одежда"):
    submission = await submissions_crud.create_submission(session, USER_ID, "text", title)
    await submissions_crud.set_item_meta(session, submission.id, USER_ID, title, category, verdict)
    return submission


async def test_add_from_analysis_keeps_verdict(session):
    """Вердикт фиксируется на момент добавления — по нему пересматривают список."""
    submission = await _submission(session)
    callback = FakeCallback(f"wishlist:add:{submission.id}")

    await add_from_analysis(callback, session)

    items = await wishlist_crud.list_items(session, USER_ID)
    assert [i.title for i in items] == ["Куртка Carhartt"]
    assert items[0].verdict == "брать"
    assert items[0].category == "верхняя одежда"
    assert items[0].source_submission_id == submission.id
    assert callback.message.markup_cleared


async def test_add_from_analysis_survives_a_stale_keyboard(session):
    """Раньше edit_reply_markup падал без try/except: сообщение с разбором
    старше 48 часов или уже нажатая кнопка обрывали хендлер прямо после
    успешной записи в БД, и пользователь не видел обновлённый список."""
    submission = await _submission(session)
    callback = FakeCallback(f"wishlist:add:{submission.id}", message_edit_fails=True)

    await add_from_analysis(callback, session)

    items = await wishlist_crud.list_items(session, USER_ID)
    assert [i.title for i in items] == ["Куртка Carhartt"]
    assert callback.message.sent, "обновлённый список должен всё равно прийти"


async def test_same_submission_not_added_twice(session):
    submission = await _submission(session)

    await add_from_analysis(FakeCallback(f"wishlist:add:{submission.id}"), session)
    second = FakeCallback(f"wishlist:add:{submission.id}")
    await add_from_analysis(second, session)

    assert len(await wishlist_crud.list_items(session, USER_ID)) == 1
    assert "уже в вишлисте" in second.answers[0]


async def test_bought_moves_item_to_wardrobe(session):
    """Вещь не должна остаться в обоих списках: куплено — значит в гардеробе."""
    submission = await _submission(session)
    await add_from_analysis(FakeCallback(f"wishlist:add:{submission.id}"), session)

    message = FakeMessage()
    await cmd_bought(message, FakeCommand("1"), session)

    assert await wishlist_crud.list_items(session, USER_ID) == []
    wardrobe = await wardrobe_crud.list_items(session, USER_ID)
    assert [i.title for i in wardrobe] == ["Куртка Carhartt"]
    assert wardrobe[0].source_submission_id == submission.id


async def test_unwish_removes_only_own_item(session):
    """Номер — позиция в своём списке, чужие вещи через него не достать."""
    await wishlist_crud.add_item(session, OTHER_USER_ID, "Чужая вещь")

    message = FakeMessage()
    await cmd_unwish(message, FakeCommand("1"), session)

    assert len(await wishlist_crud.list_items(session, OTHER_USER_ID)) == 1
    assert "пуст" in message.sent[0].lower()


async def test_manual_wish_and_listing(session):
    message = FakeMessage()
    await cmd_wish(message, FakeCommand("Куртка Carhartt, 2500 грн"), session)

    listing = FakeMessage()
    await cmd_wishlist(listing, session)

    assert "Куртка Carhartt, 2500 грн" in listing.sent[0]


async def test_empty_wishlist_explains_the_button(session):
    message = FakeMessage()
    await cmd_wishlist(message, session)

    assert "пуст" in message.sent[0]
    assert "В вишлист" in message.sent[0]


@pytest.mark.parametrize("args", [None, "", "не число", "0", "99"])
async def test_bought_without_valid_number_does_nothing(session, args):
    await wishlist_crud.add_item(session, USER_ID, "Куртка Carhartt")
    message = FakeMessage()

    await cmd_bought(message, FakeCommand(args), session)

    assert "номер" in message.sent[0].lower()
    assert len(await wishlist_crud.list_items(session, USER_ID)) == 1
    assert await wardrobe_crud.list_items(session, USER_ID) == []


async def test_numbering_stays_dense_after_removal(session):
    """Ядро проблемы: после удаления номера идут без дыр, а новая вещь
    получает следующий номер по списку, а не следующий id из БД."""
    for title in ("Первая", "Вторая", "Третья"):
        await wishlist_crud.add_item(session, USER_ID, title)

    await cmd_unwish(FakeMessage(), FakeCommand("2"), session)

    listing = FakeMessage()
    await cmd_wishlist(listing, session)
    assert "1. Первая" in listing.sent[0]
    assert "2. Третья" in listing.sent[0]

    added = FakeMessage()
    await cmd_wish(added, FakeCommand("Четвёртая"), session)
    assert "№3" in added.sent[0]

    # и номер из свежего списка адресует именно то, что показано
    await cmd_unwish(FakeMessage(), FakeCommand("2"), session)
    titles = [i.title for i in await wishlist_crud.list_items(session, USER_ID)]
    assert titles == ["Первая", "Четвёртая"]


def test_wishlist_block_absent_when_empty():
    """Пустой блок стоил бы токенов на каждом запросе."""
    prompt = PromptBuilder().build(None, [], [], [])

    assert "[ОТЛОЖЕНО К ПОКУПКЕ]" not in prompt


def test_wishlist_items_reach_prompt_with_note():
    from types import SimpleNamespace

    items = [SimpleNamespace(title="Куртка Carhartt", note="2500 грн", color=None, size=None)]
    prompt = PromptBuilder().build(None, [], [], items)

    assert "[ОТЛОЖЕНО К ПОКУПКЕ]" in prompt
    assert "- Куртка Carhartt (2500 грн)" in prompt
    # модель должна знать, что вещи ещё нет — иначе посчитает её частью гардероба
    assert "ещё нет" in prompt


# --- обновлённый список сразу после правки ------------------------------


async def test_wish_answers_with_refreshed_list(session):
    await wishlist_crud.add_item(session, USER_ID, "Первая")

    message = FakeMessage()
    await cmd_wish(message, FakeCommand("Вторая"), session)

    assert "1. Первая" in message.sent[0]
    assert "2. Вторая" in message.sent[0]


async def test_unwish_answers_with_refreshed_list(session):
    for title in ("Первая", "Вторая", "Третья"):
        await wishlist_crud.add_item(session, USER_ID, title)

    message = FakeMessage()
    await cmd_unwish(message, FakeCommand("2"), session)

    assert "1. Первая" in message.sent[0]
    assert "2. Третья" in message.sent[0]


async def test_bought_shows_both_lists(session):
    """Правка задела два списка — номера нужны из обоих."""
    await wishlist_crud.add_item(session, USER_ID, "Куртка")
    await wishlist_crud.add_item(session, USER_ID, "Кеды")

    message = FakeMessage()
    await cmd_bought(message, FakeCommand("1"), session)

    assert "Гардероб" in message.sent[0]
    assert "Вишлист" in message.sent[0]
    assert "1. Кеды" in message.sent[0], "в вишлисте номера должны сдвинуться"


async def test_wishlist_button_leaves_the_list_in_chat(session):
    """Всплывашка callback.answer исчезает — список должен остаться."""
    submission = await _submission(session)
    callback = FakeCallback(f"wishlist:add:{submission.id}")

    await add_from_analysis(callback, session)

    assert "1. Куртка Carhartt" in callback.message.sent[0]
