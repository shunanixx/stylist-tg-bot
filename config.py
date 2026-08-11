from pathlib import Path
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # Путь от файла конфига, а не от cwd: бот запускают и из корня workspace.
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent / ".env",
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

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def _split_providers(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

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


settings = Settings()
