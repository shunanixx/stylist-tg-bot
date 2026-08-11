"""Публичный режим: чужие ключи, их изоляция и хранение.

Главный риск переделки — расход, ушедший не с того ключа. Здесь он и проверяется.
"""

import pytest
import pytest_asyncio

from db.crud import users as users_crud
from db.database import Database
from services.api_keys import resolve_api_key
from services.crypto import KeyVault, derive_key, mask
from services.llm import factory

ALICE = 101
BOB = 102
OWNER = 999


class Cfg:
    """Минимальный стенд вместо настоящего Settings."""

    def __init__(self, owner_user_id=OWNER, google_api_key=None):
        self.owner_user_id = owner_user_id
        self.google_api_key = google_api_key
        self.gemini_model = "gemini-3.6-flash"


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


@pytest.fixture(autouse=True)
def clean_factory_cache():
    factory.reset_cache()
    yield
    factory.reset_cache()


# --- шифрование ---------------------------------------------------------


def test_key_is_not_stored_in_plaintext(vault):
    encrypted = vault.encrypt("AIza-секрет")

    assert "AIza-секрет" not in encrypted
    assert vault.decrypt(encrypted) == "AIza-секрет"


def test_other_master_secret_cannot_read_key(vault):
    """Утечка файла БД без мастер-секрета не должна раздавать ключи."""
    foreign = KeyVault("другой-секрет")

    assert foreign.decrypt(vault.encrypt("AIza-секрет")) is None


def test_decrypt_survives_master_secret_change(vault):
    """Смена токена бота не должна ронять бот на каждом сообщении."""
    assert KeyVault("новый-секрет").decrypt(vault.encrypt("ключ")) is None
    assert vault.decrypt(None) is None
    assert vault.decrypt("мусор") is None


def test_derive_key_is_deterministic_and_differs_per_secret():
    assert derive_key("a") == derive_key("a")
    assert derive_key("a") != derive_key("b")


def test_mask_hides_the_middle():
    assert mask("AIzaSyD-1234567890abc") == "AIza…0abc"
    assert "1234567890" not in mask("AIzaSyD-1234567890abc")
    assert mask("short") == "•" * 5


# --- чей ключ идёт в модель ---------------------------------------------


async def test_each_user_gets_own_key(session, vault):
    await users_crud.set_api_key(session, ALICE, vault.encrypt("ключ-алисы"))
    await users_crud.set_api_key(session, BOB, vault.encrypt("ключ-боба"))
    cfg = Cfg()

    alice = await users_crud.get_user(session, ALICE)
    bob = await users_crud.get_user(session, BOB)

    assert resolve_api_key(alice, vault, cfg).api_key == "ключ-алисы"
    assert resolve_api_key(bob, vault, cfg).api_key == "ключ-боба"


async def test_stranger_without_key_gets_nothing(session, vault):
    """Ключ владельца из .env не должен утекать посторонним."""
    user = await users_crud.get_or_create_user(session, ALICE)
    cfg = Cfg(google_api_key="ключ-владельца")

    source = resolve_api_key(user, vault, cfg)

    assert source.api_key is None
    assert source.present is False


async def test_owner_falls_back_to_env_key(session, vault):
    owner = await users_crud.get_or_create_user(session, OWNER)
    cfg = Cfg(google_api_key="ключ-владельца")

    source = resolve_api_key(owner, vault, cfg)

    assert source.api_key == "ключ-владельца"
    assert source.is_own is False


async def test_own_key_wins_over_env_key(session, vault):
    await users_crud.set_api_key(session, OWNER, vault.encrypt("личный-ключ"))
    owner = await users_crud.get_user(session, OWNER)

    source = resolve_api_key(owner, vault, Cfg(google_api_key="ключ-из-env"))

    assert source.api_key == "личный-ключ"
    assert source.is_own is True


async def test_removed_key_stops_being_used(session, vault):
    await users_crud.set_api_key(session, ALICE, vault.encrypt("ключ"))
    await users_crud.set_api_key(session, ALICE, None)
    user = await users_crud.get_user(session, ALICE)

    assert resolve_api_key(user, vault, Cfg()).present is False


# --- кеш провайдеров ----------------------------------------------------


def test_different_keys_get_different_clients():
    """Кеш по имени провайдера раздавал бы всем клиент на чужом ключе."""
    cfg = Cfg()

    first = factory.get_provider("gemini", cfg, "ключ-алисы")
    second = factory.get_provider("gemini", cfg, "ключ-боба")

    assert first is not second


def test_same_key_reuses_client():
    cfg = Cfg()

    assert factory.get_provider("gemini", cfg, "один-ключ") is factory.get_provider(
        "gemini", cfg, "один-ключ"
    )


def test_empty_key_is_rejected():
    with pytest.raises(ValueError, match="[Кк]люч"):
        factory.get_provider("gemini", Cfg(), "")


def test_cache_does_not_grow_without_bound():
    """Публичный бот: клиентов столько же, сколько ключей."""
    cfg = Cfg()
    for i in range(factory._MAX_CACHED + 5):
        factory.get_provider("gemini", cfg, f"ключ-{i}")

    assert len(factory._cache) <= factory._MAX_CACHED
