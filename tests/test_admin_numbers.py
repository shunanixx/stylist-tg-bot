"""Админ-команда /numbers и сбор @username.

Проверяем то, что легко разъезжается: username вообще попадает в БД (Telegram
его не хранит за нас), список показывает свежих сверху, чужое имя с разметкой
не ломает сообщение, а после /forget человека в списке нет.
"""

from types import SimpleNamespace

import pytest_asyncio

from db.crud import users as users_crud
from db.database import Database
from db.models import User
from handlers.admin import cmd_numbers
from middlewares.identity import IdentityMiddleware

OWNER_ID = 1


class FakeMessage:
    def __init__(self):
        self.sent: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.sent.append(text)
        return SimpleNamespace(message_id=len(self.sent))


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


def _tg_user(user_id: int, username=None, first_name=None):
    return SimpleNamespace(id=user_id, username=username, first_name=first_name)


# --- сбор username ------------------------------------------------------


async def test_username_is_remembered_from_any_update(session):
    """Не только /start: большинство заходит один раз и дальше жмёт кнопки."""
    middleware = IdentityMiddleware()

    async def handler(event, data):
        return "ok"

    result = await middleware(
        handler, None, {"session": session, "event_from_user": _tg_user(555, "kaa", "Кирилл")}
    )

    assert result == "ok"
    user = await users_crud.get_user(session, 555)
    assert (user.username, user.first_name) == ("kaa", "Кирилл")


async def test_changed_username_replaces_the_old_one(session):
    middleware = IdentityMiddleware()

    async def handler(event, data):
        return None

    await middleware(handler, None, {"session": session, "event_from_user": _tg_user(555, "old")})
    await middleware(handler, None, {"session": session, "event_from_user": _tg_user(555, "new")})

    user = await users_crud.get_user(session, 555)
    assert user.username == "new"


async def test_dropped_username_is_cleared(session):
    """Пользователь снял @username — в БД не должно остаться устаревшего."""
    await users_crud.remember_identity(session, 555, "kaa", "Кирилл")

    await users_crud.remember_identity(session, 555, None, "Кирилл")

    user = await users_crud.get_user(session, 555)
    assert user.username is None


async def test_handler_runs_even_if_identity_fails(session, monkeypatch):
    """Запись имени — вспомогательная: ответ пользователю из-за неё не падает."""

    async def boom(*args, **kwargs):
        raise RuntimeError("БД недоступна")

    monkeypatch.setattr(users_crud, "remember_identity", boom)

    async def handler(event, data):
        return "ответ"

    result = await IdentityMiddleware()(
        handler, None, {"session": session, "event_from_user": _tg_user(555, "kaa")}
    )

    assert result == "ответ"


async def test_failed_identity_write_leaves_a_usable_session(session, monkeypatch):
    """Упавший flush ломает транзакцию целиком: без rollback хендлер падал бы
    на любом обращении к БД — «функция не работает» на ровном месте."""

    async def boom(session_arg, *args, **kwargs):
        # Дубль первичного ключа: ошибка на flush, как у remember_identity
        session_arg.add(User(user_id=555))
        session_arg.add(User(user_id=555))
        await session_arg.flush()

    await users_crud.get_or_create_user(session, 555)
    monkeypatch.setattr(users_crud, "remember_identity", boom)
    handled = []

    async def handler(event, data):
        # Ровно то, что делает любой хендлер после middleware
        handled.append(await users_crud.get_or_create_user(session, 555))
        return "ответ"

    result = await IdentityMiddleware()(
        handler, None, {"session": session, "event_from_user": _tg_user(555, "kaa")}
    )

    assert result == "ответ"
    assert handled and handled[0].user_id == 555


# --- /numbers ----------------------------------------------------------


async def test_numbers_shows_username_name_and_id(session):
    await users_crud.remember_identity(session, 555, "kaa", "Кирилл")
    message = FakeMessage()

    await cmd_numbers(message, session)

    text = "\n".join(message.sent)
    assert "@kaa" in text
    assert "Кирилл" in text
    assert "<code>555</code>" in text
    assert "разборов нет" in text
    assert "🔑 нет" in text


async def test_numbers_marks_users_without_username(session):
    await users_crud.remember_identity(session, 777, None, "Аня")
    await users_crud.remember_identity(session, 778, None, None)
    message = FakeMessage()

    await cmd_numbers(message, session)

    text = "\n".join(message.sent)
    assert "Аня (без username)" in text
    assert "без username и имени" in text


async def test_numbers_escapes_names_with_markup(session):
    """Имя приходит от пользователя, а parse_mode у бота HTML."""
    await users_crud.remember_identity(session, 555, None, "<b>взлом</b>")
    message = FakeMessage()

    await cmd_numbers(message, session)

    text = "\n".join(message.sent)
    assert "<b>взлом</b>" not in text
    assert "&lt;b&gt;взлом&lt;/b&gt;" in text


async def test_numbers_puts_the_newest_first(session):
    for user_id, name in ((501, "первый"), (502, "второй"), (503, "третий")):
        await users_crud.remember_identity(session, user_id, None, name)
    message = FakeMessage()

    await cmd_numbers(message, session)

    text = "\n".join(message.sent)
    assert text.index("третий") < text.index("первый")


async def test_numbers_counts_submissions_and_key(session, encrypted_key):
    from db.crud import submissions as submissions_crud

    await users_crud.remember_identity(session, 555, "kaa", "Кирилл")
    await users_crud.set_api_key(session, 555, encrypted_key)
    for _ in range(2):
        await submissions_crud.create_submission(session, 555, input_type="text")
    message = FakeMessage()

    await cmd_numbers(message, session)

    text = "\n".join(message.sent)
    assert "разборов 2" in text
    assert "🔑 есть" in text


async def test_numbers_on_empty_base(session):
    message = FakeMessage()

    await cmd_numbers(message, session)

    assert message.sent == ["Пользователей пока нет."]


async def test_forgotten_user_disappears_from_the_list(session):
    await users_crud.remember_identity(session, 555, "kaa", "Кирилл")
    await users_crud.delete_user(session, 555)
    message = FakeMessage()

    await cmd_numbers(message, session)

    assert message.sent == ["Пользователей пока нет."]


async def test_numbers_says_that_phones_are_unavailable(session):
    """Чтобы через месяц не искать, почему в списке нет телефонов."""
    await users_crud.remember_identity(session, 555, "kaa", None)
    message = FakeMessage()

    await cmd_numbers(message, session)

    assert "Поделиться контактом" in "\n".join(message.sent)


async def test_numbers_caps_a_long_list(session, monkeypatch):
    from handlers import admin

    monkeypatch.setattr(admin, "NUMBERS_LIMIT", 2)
    for user_id in range(600, 605):
        await users_crud.remember_identity(session, user_id, f"u{user_id}", None)
    message = FakeMessage()

    await cmd_numbers(message, session)

    text = "\n".join(message.sent)
    assert "Показал последних 2 из 5." in text
