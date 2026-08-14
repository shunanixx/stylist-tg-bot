"""Настройки деплоя: режим работы, адрес вебхука, строка подключения к БД.

Всё это задаётся в панели хостинга и проверяется только в бою, где ошибка
выглядит как «бот молчит». Поэтому разбор строки Postgres и валидацию режима
держим тестами: правки в config.py не должны их менять молча.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import NullPool

from config import Settings
from db.database import engine_options

TOKEN = "123456789:AAHfake-token-for-tests"


def _settings(**kwargs) -> Settings:
    # _env_file=None: настройки деплоя проверяем в изоляции от личного .env,
    # иначе тест зависит от того, что владелец вписал себе локально.
    return Settings(_env_file=None, telegram_bot_token=TOKEN, **kwargs)


# --- строка подключения к Postgres --------------------------------------


def test_psycopg_scheme_becomes_asyncpg():
    """Панели отдают postgres://… — синхронному psycopg2, а движок у нас async."""
    url = _settings(database_url="postgres://user:pass@host/db").database_url

    assert url.startswith("postgresql+asyncpg://")
    assert "user:pass@host/db" in url


def test_postgresql_scheme_also_gets_the_driver():
    assert _settings(database_url="postgresql://user:pass@host/db").database_url == (
        "postgresql+asyncpg://user:pass@host/db"
    )


def test_sslmode_becomes_ssl():
    """asyncpg не знает libpq-параметров и падает на неизвестном аргументе."""
    url = _settings(database_url="postgres://u:p@host/db?sslmode=require").database_url

    assert url == "postgresql+asyncpg://u:p@host/db?ssl=require"


def test_channel_binding_and_other_libpq_params_are_dropped():
    """Neon добавляет channel_binding, Supabase — свои: asyncpg их не примет."""
    url = _settings(
        database_url="postgres://u:p@host/db?sslmode=verify-full&channel_binding=require"
    ).database_url

    assert url == "postgresql+asyncpg://u:p@host/db?ssl=verify-full"


def test_asyncpg_url_is_left_as_is():
    url = "postgresql+asyncpg://u:p@host/db"

    assert _settings(database_url=url).database_url == url


def test_sqlite_url_is_not_touched_by_the_postgres_rule():
    assert _settings(database_url="sqlite+aiosqlite:///:memory:").database_url == (
        "sqlite+aiosqlite:///:memory:"
    )


def test_postgres_engine_gets_no_pool():
    """Открытый пул не даёт serverless-базе заснуть — и жжёт её бесплатную квоту."""
    assert engine_options("postgresql+asyncpg://u:p@host/db") == {"poolclass": NullPool}


def test_sqlite_engine_keeps_the_default_pool():
    assert engine_options("sqlite+aiosqlite:///./stylist_bot.db") == {}


def test_normalized_url_gives_asyncpg_only_arguments_it_knows():
    """Проверка не по нашим догадкам, а по настоящему диалекту и подписи asyncpg.

    Строка из панели (`?sslmode=require&channel_binding=require`) доезжает до
    `asyncpg.connect()` как есть и роняет первое же подключение на неизвестном
    аргументе — и видно это только в бою.
    """
    import inspect

    import asyncpg
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.engine import make_url

    raw = "postgres://u:p@host/db?sslmode=require&channel_binding=require"
    url = make_url(_settings(database_url=raw).database_url)
    dialect = postgresql.dialect.get_dialect_cls(url)()

    _, connect_kwargs = dialect.create_connect_args(url)
    # dbname диалект переводит в database сам, остальное уходит в connect()
    unknown = set(connect_kwargs) - set(inspect.signature(asyncpg.connect).parameters) - {"dbname"}
    assert unknown == set()
    assert connect_kwargs["ssl"] == "require"


def test_schema_is_portable_to_postgres():
    """Структура из раздела 4 должна создаться и на Postgres, не только в SQLite.

    Схему по-прежнему заводит `create_all` (Alembic — позже), а на хостинге
    это первый же запуск: несовместимый тип уронил бы бота на старте.
    """
    from sqlalchemy import create_mock_engine

    from db.models import Base

    statements: list[str] = []
    engine = create_mock_engine(
        "postgresql+asyncpg://", lambda sql, *args, **kwargs: statements.append(str(sql))
    )
    Base.metadata.create_all(engine, checkfirst=False)
    ddl = "\n".join(statements)

    for table in (
        "users",
        "style_items",
        "wardrobe_items",
        "wishlist_items",
        "submissions",
        "submission_results",
        "conversation_followups",
        "chat_messages",
    ):
        assert f"CREATE TABLE {table} (" in ddl
    # Ключи пользователей и id Telegram: TEXT и BIGINT, а не VARCHAR(n)/INT
    assert "google_api_key_enc TEXT" in ddl
    assert "user_id BIGSERIAL" not in ddl and "user_id BIGINT NOT NULL" in ddl


# --- режим работы -------------------------------------------------------


def test_polling_is_the_default():
    """Локальный запуск не должен требовать ни одной новой переменной."""
    assert _settings().run_mode == "polling"


def test_webhook_mode_requires_an_address():
    with pytest.raises(ValidationError, match="WEBHOOK_BASE_URL"):
        _settings(run_mode="webhook")


def test_webhook_mode_rejects_plain_http():
    with pytest.raises(ValidationError, match="https"):
        _settings(run_mode="webhook", webhook_base_url="http://example.com")


def test_unknown_run_mode_is_rejected():
    with pytest.raises(ValidationError):
        _settings(run_mode="systemd")


def test_polling_mode_does_not_demand_webhook_settings():
    assert _settings(run_mode="polling", webhook_base_url=None).webhook_base_url is None


# --- адрес и секрет вебхука --------------------------------------------


def test_webhook_url_joins_base_and_path():
    cfg = _settings(run_mode="webhook", webhook_base_url="https://bot.example.com")

    assert cfg.webhook_url == "https://bot.example.com/telegram/webhook"


def test_trailing_slash_does_not_double_up():
    """RENDER_EXTERNAL_URL приходит без слеша, но руками его вписывают по-разному."""
    cfg = _settings(run_mode="webhook", webhook_base_url="https://bot.example.com/")

    assert cfg.webhook_url == "https://bot.example.com/telegram/webhook"


def test_address_is_taken_from_render_external_url(monkeypatch):
    """Свой адрес сервис узнаёт только после создания — Render кладёт его в окружение."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://stylist-tg-bot.onrender.com")

    cfg = _settings(run_mode="webhook")

    assert cfg.webhook_url == "https://stylist-tg-bot.onrender.com/telegram/webhook"


def test_explicit_base_url_wins_over_the_platform_variable(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://render.example.com")

    cfg = _settings(run_mode="webhook", webhook_base_url="https://own.example.com")

    assert cfg.webhook_url.startswith("https://own.example.com")


def test_secret_is_derived_from_the_token_when_not_set():
    """Без секрета поддельный апдейт может прислать кто угодно, кто знает путь."""
    secret = _settings().resolved_webhook_secret

    assert len(secret) == 32
    assert secret == _settings().resolved_webhook_secret
    assert TOKEN not in secret


def test_different_tokens_give_different_secrets():
    other = Settings(_env_file=None, telegram_bot_token="987654321:AAHanother-fake-token")

    assert _settings().resolved_webhook_secret != other.resolved_webhook_secret


def test_explicit_secret_is_used_as_is():
    assert _settings(webhook_secret="fake-secret").resolved_webhook_secret == "fake-secret"
