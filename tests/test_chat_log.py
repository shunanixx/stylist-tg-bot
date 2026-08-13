"""Учёт id сообщений: без него /clear нечего удалять."""

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from db.crud import chat_log as chat_log_crud
from db.crud import users as users_crud
from db.database import Database
from middlewares.chat_log import ChatLogMiddleware
from services.chat_tracker import record, start_recording, stop_recording

USER_ID = 9090
OTHER_USER_ID = 9091
CHAT_ID = 700


class FakeChat:
    def __init__(self, chat_id: int = CHAT_ID):
        self.id = chat_id


class FakeMessage:
    """Подделка aiogram.types.Message: middleware различает типы по isinstance,
    поэтому в тестах подменяем сам класс через monkeypatch."""

    def __init__(self, message_id: int, chat_id: int = CHAT_ID):
        self.message_id = message_id
        self.chat = FakeChat(chat_id)


class FakeCallback:
    def __init__(self, message: FakeMessage):
        self.message = message


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        # Строку пользователя создаёт IdentityMiddleware до хендлера — журнал
        # на неё только ссылается и сам никого не создаёт (иначе /forget
        # воскрешал бы удалённого).
        await users_crud.get_or_create_user(session, USER_ID)
        yield session
    await database.dispose()


@pytest_asyncio.fixture
async def middleware(monkeypatch):
    monkeypatch.setattr("middlewares.chat_log.Message", FakeMessage)
    return ChatLogMiddleware()


def _data(session):
    return {"session": session, "event_from_user": type("U", (), {"id": USER_ID})()}


async def test_incoming_and_outgoing_are_both_tracked(session, middleware):
    """Мусор в чате — и вопрос пользователя, и ответ бота."""

    async def handler(event, data):
        record(CHAT_ID, 101)  # что бот отправил в ответ
        return None

    await middleware(handler, FakeMessage(100), _data(session))

    assert await chat_log_crud.list_ids(session, USER_ID, CHAT_ID) == [101, 100]


async def test_several_replies_all_tracked(session, middleware):
    """Длинный разбор режется на части — учитывать надо все."""

    async def handler(event, data):
        for message_id in (201, 202, 203):
            record(CHAT_ID, message_id)

    await middleware(handler, FakeMessage(200), _data(session))

    assert await chat_log_crud.list_ids(session, USER_ID, CHAT_ID) == [203, 202, 201, 200]


async def test_callback_does_not_double_count_its_message(session, middleware):
    """У callback сообщение — наше собственное, уже записанное при отправке."""
    await chat_log_crud.remember_many(session, USER_ID, [(CHAT_ID, 300)])

    async def handler(event, data):
        return None

    await middleware(handler, FakeCallback(FakeMessage(300)), _data(session))

    assert await chat_log_crud.list_ids(session, USER_ID, CHAT_ID) == [300]


async def test_handler_failure_does_not_lose_tracking(session, middleware):
    """Ответ об ошибке тоже висит в чате и тоже должен чиститься."""

    async def handler(event, data):
        record(CHAT_ID, 401)
        raise RuntimeError("провайдер упал")

    with pytest.raises(RuntimeError):
        await middleware(handler, FakeMessage(400), _data(session))


async def test_journal_write_failure_does_not_break_reply(session, middleware, monkeypatch):
    """Учёт — вспомогательная вещь: из-за него ответ падать не должен."""

    async def boom(*args, **kwargs):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(chat_log_crud, "remember_many", boom)

    async def handler(event, data):
        record(CHAT_ID, 501)
        return "ответ доставлен"

    result = await middleware(handler, FakeMessage(500), _data(session))

    assert result == "ответ доставлен"


async def test_buffers_do_not_leak_between_concurrent_updates():
    """Два пользователя пишут одновременно — их id не должны смешаться."""

    async def one(chat_id: int, message_id: int) -> list[tuple[int, int]]:
        buffer = start_recording()
        await asyncio.sleep(0)  # уступаем управление второй задаче
        record(chat_id, message_id)
        await asyncio.sleep(0)
        stop_recording()
        return buffer

    first, second = await asyncio.gather(one(1, 11), one(2, 22))

    assert first == [(1, 11)]
    assert second == [(2, 22)]


async def test_record_outside_recording_is_ignored():
    """Фоновая отправка вне апдейта не должна падать."""
    stop_recording()
    record(CHAT_ID, 999)  # не бросает


async def test_message_is_found_inside_a_real_update(session):
    """Middleware висит на dispatcher.update — приходит Update, а не Message.

    Без разворачивания апдейта в журнал не попадало ни одно сообщение
    пользователя, и /clear всегда отвечал «чистить нечего».
    """
    update = Update(
        update_id=1,
        message=Message(
            message_id=600,
            date=datetime.now(timezone.utc),
            chat=Chat(id=CHAT_ID, type="private"),
            from_user=TgUser(id=USER_ID, is_bot=False, first_name="Тест"),
            text="привет",
        ),
    )

    async def handler(event, data):
        record(CHAT_ID, 601)

    await ChatLogMiddleware()(handler, update, _data(session))

    assert await chat_log_crud.list_ids(session, USER_ID, CHAT_ID) == [601, 600]


async def test_journal_does_not_resurrect_a_forgotten_user(session, middleware):
    """/forget удаляет строку, а журнал пишется после хендлера — раньше он
    тут же создавал её заново, и человек возвращался в /numbers."""

    async def handler(event, data):
        await users_crud.delete_user(session, USER_ID)
        record(CHAT_ID, 701)

    await middleware(handler, FakeMessage(700), _data(session))

    assert await users_crud.get_user(session, USER_ID) is None
    assert await chat_log_crud.list_ids(session, USER_ID, CHAT_ID) == []
