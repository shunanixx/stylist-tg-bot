"""Очистка чата: сообщения уходят, данные остаются."""

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramBadRequest

from db.crud import chat_log as chat_log_crud
from db.crud import styles as styles_crud
from db.crud import submissions as submissions_crud
from db.crud import users as users_crud
from db.crud import wardrobe as wardrobe_crud
from db.crud import wishlist as wishlist_crud
from db.database import Database
from handlers.cleanup import cancel_clear, cmd_clear, confirm_clear, delete_messages

USER_ID = 8080
OTHER_USER_ID = 8081
CHAT_ID = 500
OTHER_CHAT_ID = 501


class FakeChat:
    def __init__(self, chat_id: int = CHAT_ID):
        self.id = chat_id


class FakeMessage:
    def __init__(self, chat_id: int = CHAT_ID, user_id: int = USER_ID, message_id: int = 1):
        self.chat = FakeChat(chat_id)
        self.from_user = type("U", (), {"id": user_id})()
        self.message_id = message_id
        self.sent: list[str] = []
        self.edits: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.sent.append(text)
        return self

    async def edit_text(self, text: str, **kwargs):
        self.edits.append(text)


class FakeCallback:
    def __init__(self, message: FakeMessage, user_id: int = USER_ID):
        self.message = message
        self.from_user = type("U", (), {"id": user_id})()
        self.answers: list[str] = []

    async def answer(self, text: str = "", **kwargs):
        self.answers.append(text)


class FakeBot:
    """Считает удалённое. too_old — сообщения, которые Telegram не отдаёт."""

    def __init__(self, too_old: set[int] | None = None, batch_fails: bool = False):
        self._too_old = too_old or set()
        self._batch_fails = batch_fails
        self.deleted: list[int] = []
        self.batch_calls = 0
        self.single_calls = 0
        self.sent: list[str] = []

    async def delete_messages(self, chat_id: int, message_ids: list[int]):
        self.batch_calls += 1
        if self._batch_fails or self._too_old & set(message_ids):
            raise TelegramBadRequest(method=None, message="message can't be deleted")
        self.deleted.extend(message_ids)
        return True

    async def delete_message(self, chat_id: int, message_id: int):
        self.single_calls += 1
        if message_id in self._too_old:
            raise TelegramBadRequest(method=None, message="message can't be deleted")
        self.deleted.append(message_id)
        return True

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append(text)
        return FakeMessage(chat_id)


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


async def _track(session, ids, user_id=USER_ID, chat_id=CHAT_ID):
    await users_crud.get_or_create_user(session, user_id)
    await chat_log_crud.remember_many(
        session, user_id, [(chat_id, message_id) for message_id in ids]
    )


# --- главное: данные переживают очистку ---------------------------------


async def test_clear_removes_messages_but_keeps_data(session):
    """Смысл команды: чат чистый, история разборов и вещи на месте."""
    await _track(session, [10, 11, 12])
    submission = await submissions_crud.create_submission(
        session, USER_ID, "text", "Куртка Carhartt"
    )
    await submissions_crud.set_item_meta(
        session, submission.id, "Куртка Carhartt", "верхняя одежда", "брать"
    )
    await wardrobe_crud.add_item(session, USER_ID, "Серый свитшот")
    await wishlist_crud.add_item(session, USER_ID, "Кеды Puma")
    await styles_crud.add_style(session, USER_ID, "gorpcore")
    await users_crud.update_measurements(session, USER_ID, height_cm=175)

    bot = FakeBot()
    await confirm_clear(FakeCallback(FakeMessage(message_id=99)), session, bot)

    # 99 — само сообщение с кнопками, оно уходит последним
    assert set(bot.deleted) == {10, 11, 12, 99}
    # ничего из данных не пострадало
    assert len(await submissions_crud.recent_submissions(session, USER_ID)) == 1
    assert len(await wardrobe_crud.list_items(session, USER_ID)) == 1
    assert len(await wishlist_crud.list_items(session, USER_ID)) == 1
    assert len(await styles_crud.list_styles(session, USER_ID)) == 1
    user = await users_crud.get_user(session, USER_ID)
    assert user.height_cm == 175


async def test_api_key_survives_clear(session, encrypted_key):
    """Очистка чата не должна разлогинивать — ключ остаётся."""
    await users_crud.set_api_key(session, USER_ID, encrypted_key)
    await _track(session, [10])

    await confirm_clear(FakeCallback(FakeMessage(message_id=99)), session, FakeBot())

    user = await users_crud.get_user(session, USER_ID)
    assert user.google_api_key_enc == encrypted_key


async def test_journal_is_emptied_after_clear(session):
    await _track(session, [10, 11])

    await confirm_clear(FakeCallback(FakeMessage(message_id=99)), session, FakeBot())

    assert await chat_log_crud.list_ids(session, USER_ID, CHAT_ID) == []


# --- изоляция -----------------------------------------------------------


async def test_other_users_messages_are_not_touched(session):
    await _track(session, [10, 11])
    await _track(session, [20, 21], user_id=OTHER_USER_ID)

    bot = FakeBot()
    await confirm_clear(FakeCallback(FakeMessage(message_id=99)), session, bot)

    assert set(bot.deleted) == {10, 11, 99}
    assert 20 not in bot.deleted and 21 not in bot.deleted
    assert await chat_log_crud.list_ids(session, OTHER_USER_ID, CHAT_ID) == [21, 20]


async def test_other_chat_of_same_user_is_not_touched(session):
    await _track(session, [10])
    await _track(session, [30], chat_id=OTHER_CHAT_ID)

    bot = FakeBot()
    await confirm_clear(FakeCallback(FakeMessage(message_id=99)), session, bot)

    assert set(bot.deleted) == {10, 99}
    assert 30 not in bot.deleted
    assert await chat_log_crud.list_ids(session, USER_ID, OTHER_CHAT_ID) == [30]


# --- подтверждение ------------------------------------------------------


async def test_clear_asks_before_deleting(session):
    await _track(session, [10, 11])
    message = FakeMessage()

    await cmd_clear(message, session)

    assert "2" in message.sent[0]
    assert await chat_log_crud.list_ids(session, USER_ID, CHAT_ID) == [11, 10]


async def test_cancel_keeps_everything(session):
    await _track(session, [10, 11])
    message = FakeMessage(message_id=99)

    await cancel_clear(FakeCallback(message))

    assert await chat_log_crud.list_ids(session, USER_ID, CHAT_ID) == [11, 10]
    assert "Отменил" in message.edits[0]


async def test_nothing_to_clear_explains_48h_limit(session):
    message = FakeMessage()

    await cmd_clear(message, session)

    assert "48" in message.sent[0]


# --- пачки и старые сообщения -------------------------------------------


async def test_deletes_in_batches_of_100():
    bot = FakeBot()

    deleted, failed = await delete_messages(bot, CHAT_ID, list(range(1, 251)))

    assert (deleted, failed) == (250, 0)
    assert bot.batch_calls == 3


async def test_one_old_message_does_not_block_the_batch():
    """Пачка падает целиком из-за одного старого сообщения — добиваем поштучно."""
    bot = FakeBot(too_old={5})

    deleted, failed = await delete_messages(bot, CHAT_ID, list(range(1, 11)))

    assert (deleted, failed) == (9, 1)
    assert 5 not in bot.deleted
    assert bot.single_calls == 10


async def test_report_mentions_undeletable_messages(session):
    await _track(session, [10, 11])
    bot = FakeBot(too_old={11})

    await confirm_clear(FakeCallback(FakeMessage(message_id=99)), session, bot)

    assert "48" in bot.sent[0]


@pytest.mark.parametrize("count", [0, 1])
async def test_confirmation_message_is_deleted_last(session, count):
    """Сообщение с кнопками — единственный признак, что бот занят."""
    await _track(session, [10] if count else [])
    bot = FakeBot()

    await confirm_clear(FakeCallback(FakeMessage(message_id=99)), session, bot)

    assert bot.deleted[-1] == 99
