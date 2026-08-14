import hashlib
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_DIR = Path(__file__).parent

# Схемы, которые хостинги выдают для libpq/psycopg: движок у нас async,
# и без явного драйвера SQLAlchemy возьмёт синхронный psycopg2.
_SYNC_POSTGRES_SCHEMES = ("postgres", "postgresql", "postgresql+psycopg2", "postgresql+psycopg")


def _asyncpg_url(url: str) -> str:
    """Приводит строку подключения Postgres к asyncpg и чистит параметры libpq.

    Neon, Supabase и Render отдают адрес в формате psycopg: `postgres://…`
    с `?sslmode=require&channel_binding=require`. asyncpg передаёт такие
    параметры в `connect()` как есть и падает на неизвестном аргументе,
    поэтому `sslmode` переводим в понятный ему `ssl`, остальное отбрасываем —
    TLS у managed-Postgres включён и без них.
    """
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    ssl_mode = params.get("ssl") or params.get("sslmode")
    query = urlencode({"ssl": ssl_mode}) if ssl_mode else ""
    scheme = "postgresql+asyncpg" if parts.scheme in _SYNC_POSTGRES_SCHEMES else parts.scheme
    return urlunsplit((scheme, parts.netloc, parts.path, query, parts.fragment))


class Settings(BaseSettings):
    # Путь от файла конфига, а не от cwd: бот запускают и из корня workspace.
    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str
    # Владелец: доступ к /stats и работа без личного ключа. 0 — владельца нет.
    owner_user_id: int = 0

    # Бот публичный: ключ у каждого свой (/apikey). Этот — резерв владельца,
    # поэтому необязателен.
    google_api_key: str | None = None
    # На free tier у 2.5-pro квота 0, а 2.5-flash закрыт для новых проектов.
    gemini_model: str = "gemini-3.6-flash"

    # Мастер-секрет для шифрования чужих ключей в БД. Пусто — выводится из
    # токена бота (см. services/crypto.py).
    encryption_key: str | None = None

    # DeepSeek (этап 2)
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # Kimi (этап 2)
    moonshot_api_key: str | None = None
    kimi_model: str = "kimi-k2-0905-preview"
    kimi_base_url: str = "https://api.moonshot.ai/v1"

    # По умолчанию выключены — резерв (этап 5)
    anthropic_api_key: str | None = None
    claude_model: str = "claude-sonnet-4-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5"

    default_llm_provider: str = "gemini"
    # Расширяется по мере разработки. В .env — список через запятую.
    enabled_providers: Annotated[list[str], NoDecode] = ["gemini"]

    max_concurrent_agents: int = 2
    max_photos_per_analysis: int = 10

    database_url: str = "sqlite+aiosqlite:///./stylist_bot.db"
    log_level: str = "INFO"

    # Как получаем апдейты. polling — локально и на любом always-on хосте;
    # webhook — на бесплатных PaaS, где процесс обязан слушать HTTP-порт,
    # а фоновые воркеры платные.
    run_mode: Literal["polling", "webhook"] = "polling"
    # Внешний https-адрес сервиса. До первого деплоя он неизвестен, но Render
    # сам кладёт его в RENDER_EXTERNAL_URL — берём оттуда, чтобы не вписывать
    # адрес руками и не расходиться с реальностью после пересоздания сервиса.
    webhook_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("WEBHOOK_BASE_URL", "RENDER_EXTERNAL_URL"),
    )
    webhook_path: str = "/telegram/webhook"
    # Заголовок X-Telegram-Bot-Api-Secret-Token. Пусто — выводится из токена
    # бота, как ENCRYPTION_KEY.
    webhook_secret: str | None = None
    webhook_host: str = "0.0.0.0"
    # Порт диктует хостинг (PORT в окружении): слушать нужно именно его,
    # иначе сервис считают мёртвым и деплой падает.
    port: int = 10000

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def _split_providers(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _async_postgres_driver(cls, value: str) -> str:
        """Postgres приезжает из панели хостинга — приводим к asyncpg сами.

        Иначе первая же попытка подключиться падает либо на синхронном
        драйвере, либо на `sslmode`, которого asyncpg не знает.
        """
        if value.startswith("postgres"):
            return _asyncpg_url(value)
        return value

    @field_validator("database_url")
    @classmethod
    def _absolute_sqlite_path(cls, value: str) -> str:
        """Файл БД — рядом с проектом, а не рядом с cwd.

        Относительный путь резолвится от рабочего каталога, и запуск из корня
        workspace (отладчиком VS Code, `python "cloth ai/bot.py"`) заводил
        вторую базу: команды отвечают, а гардероб, ключи и история пустые.
        """
        prefix, separator, tail = value.partition(":///")
        if not separator or not prefix.startswith("sqlite") or tail.startswith("/"):
            return value
        if tail in ("", ":memory:"):
            return value
        return f"{prefix}:///{(PROJECT_DIR / tail).resolve()}"

    @model_validator(mode="after")
    def validate_default_provider_enabled(self) -> "Settings":
        if not self.enabled_providers:
            raise ValueError("ENABLED_PROVIDERS не может быть пустым")
        if self.default_llm_provider not in self.enabled_providers:
            raise ValueError(
                f"DEFAULT_LLM_PROVIDER='{self.default_llm_provider}' должен быть "
                f"в ENABLED_PROVIDERS={self.enabled_providers}"
            )
        return self

    @model_validator(mode="after")
    def validate_webhook_settings(self) -> "Settings":
        """Ошибка режима вебхука должна быть видна на старте.

        Без адреса `set_webhook` уходит в Telegram с мусором, бот молчит,
        а в логах — только «сервис жив»: искать причину потом дорого.
        """
        if self.run_mode != "webhook":
            return self
        if not self.webhook_base_url:
            raise ValueError(
                "RUN_MODE=webhook требует WEBHOOK_BASE_URL — внешний https-адрес сервиса"
            )
        if not self.webhook_base_url.startswith("https://"):
            raise ValueError("Telegram принимает вебхук только по https")
        if not self.webhook_path.startswith("/"):
            raise ValueError("WEBHOOK_PATH должен начинаться со /")
        return self

    @property
    def webhook_url(self) -> str:
        """Адрес, который получит Telegram."""
        return f"{(self.webhook_base_url or '').rstrip('/')}{self.webhook_path}"

    @property
    def resolved_webhook_secret(self) -> str:
        """Секрет для заголовка от Telegram.

        Путь вебхука рано или поздно всплывёт в логах прокси, и без секрета
        поддельный апдейт сможет отправить кто угодно. Не задан — выводим из
        токена бота, как и ENCRYPTION_KEY: у токена и вебхука одна судьба,
        смена токена всё равно требует переустановки вебхука.
        """
        if self.webhook_secret:
            return self.webhook_secret
        return hashlib.sha256(self.telegram_bot_token.encode()).hexdigest()[:32]


settings = Settings()
