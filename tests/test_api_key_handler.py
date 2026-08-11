"""Хендлер /apikey: приём ключа, зачистка следов, снятие."""

import pytest
import pytest_asyncio

from db.crud import users as users_crud
from db.database import Database
from handlers.api_key import cmd_apikey, cmd_apikey_off
from services.api_keys import resolve_api_key

USER_ID = 777
GOOD_KEY = "AIzaSyD-abcdefghijklmnopqrstuvwxyz012345"


class FakeMessage:
    def __init__(self, text: str, user_id: int = USER_ID):
        self.text = text
        self.from_user = type("U", (), {"id": user_id})()
        self.sent: list[str] = []
        self.deleted = False

    async def answer(self, text: str, **kwargs):
        self.sent.append(text)
        return self

    async def delete(self):
        self.deleted = True


class Cfg:
    owner_user_id = 1
    google_api_key = None
    gemini_model = "gemini-3.6-flash"


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


@pytest.fixture
def probe_ok(monkeypatch):
    async def _ok(api_key, settings):
        return None

    monkeypatch.setattr("handlers.api_key.probe_key", _ok)


@pytest.fixture
def probe_fails(monkeypatch):
    async def _fail(api_key, settings):
        return "ключ отклонён"

    monkeypatch.setattr("handlers.api_key.probe_key", _fail)


async def test_valid_key_is_saved_and_message_deleted(session, vault, probe_ok):
    """Сообщение с ключом обязано исчезнуть из чата."""
    message = FakeMessage(f"/apikey {GOOD_KEY}")

    await cmd_apikey(message, session, vault, Cfg())

    assert message.deleted, "ключ остался в истории чата"
    user = await users_crud.get_user(session, USER_ID)
    assert resolve_api_key(user, vault, Cfg()).api_key == GOOD_KEY


async def test_key_is_never_echoed_back(session, vault, probe_ok):
    message = FakeMessage(f"/apikey {GOOD_KEY}")

    await cmd_apikey(message, session, vault, Cfg())

    assert all(GOOD_KEY not in text for text in message.sent)


async def test_bad_key_is_not_saved(session, vault, probe_fails):
    """Проверка живым вызовом: нерабочий ключ до БД не доходит."""
    message = FakeMessage(f"/apikey {GOOD_KEY}")

    await cmd_apikey(message, session, vault, Cfg())

    assert message.deleted
    user = await users_crud.get_user(session, USER_ID)
    assert user is None or user.google_api_key_enc is None


async def test_obviously_wrong_key_rejected_before_network(session, vault, monkeypatch):
    async def _boom(api_key, settings):
        raise AssertionError("сетевой вызов на явно неверном ключе")

    monkeypatch.setattr("handlers.api_key.probe_key", _boom)
    message = FakeMessage("/apikey 123")

    await cmd_apikey(message, session, vault, Cfg())

    assert "не похож" in message.sent[0]


async def test_new_format_key_is_accepted(session, vault, probe_ok):
    """AI Studio выдаёт и «AQ.…» — проверка префикса отсекала бы рабочий ключ."""
    new_format = "AQ.Fake-Test-Key-0000000000000000000000000000"
    message = FakeMessage(f"/apikey {new_format}")

    await cmd_apikey(message, session, vault, Cfg())

    user = await users_crud.get_user(session, USER_ID)
    assert resolve_api_key(user, vault, Cfg()).api_key == new_format


async def test_bare_command_shows_instructions(session, vault):
    message = FakeMessage("/apikey")

    await cmd_apikey(message, session, vault, Cfg())

    assert "aistudio.google.com" in message.sent[0]
    assert not message.deleted, "нечего удалять — ключа в сообщении не было"


async def test_bare_command_reports_existing_key_masked(session, vault):
    await users_crud.set_api_key(session, USER_ID, vault.encrypt(GOOD_KEY))
    message = FakeMessage("/apikey")

    await cmd_apikey(message, session, vault, Cfg())

    assert "AIza…2345" in message.sent[0]
    assert GOOD_KEY not in message.sent[0]


async def test_apikey_off_removes_key(session, vault):
    await users_crud.set_api_key(session, USER_ID, vault.encrypt(GOOD_KEY))
    message = FakeMessage("/apikey_off")

    await cmd_apikey_off(message, session, vault, Cfg())

    user = await users_crud.get_user(session, USER_ID)
    assert user.google_api_key_enc is None


async def test_apikey_off_without_key_is_not_an_error(session, vault):
    message = FakeMessage("/apikey_off")

    await cmd_apikey_off(message, session, vault, Cfg())

    assert message.sent, "ответ нужен даже когда удалять нечего"
