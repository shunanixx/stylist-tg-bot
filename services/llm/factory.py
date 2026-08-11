import hashlib
from collections.abc import Callable

from config import Settings
from services.llm.base import LLMProvider
from services.llm.gemini_provider import GeminiProvider

IMPLEMENTED: tuple[str, ...] = ("gemini",)

# Провайдер -> этап roadmap, на котором он появится
PLANNED: dict[str, str] = {
    "deepseek": "этап 2",
    "kimi": "этап 2",
    "claude": "этап 5",
    "openai": "этап 5",
}


def _build_gemini(cfg: Settings, api_key: str) -> LLMProvider:
    return GeminiProvider(api_key=api_key, model=cfg.gemini_model)


_BUILDERS: dict[str, Callable[[Settings, str], LLMProvider]] = {
    "gemini": _build_gemini,
}

# Ключ кеша — (провайдер, хеш ключа), а не имя провайдера: бот публичный, у
# каждого свой ключ, и кеш по одному имени отдавал бы всем клиент, собранный
# на чужом ключе — расход шёл бы не тому владельцу квоты. Хешируем, чтобы
# чужие ключи не лежали ещё и в ключах словаря.
_cache: dict[tuple[str, str], LLMProvider] = {}
# Клиентов столько же, сколько активных ключей — страховка от роста памяти.
_MAX_CACHED = 256


def get_provider(name: str, cfg: Settings, api_key: str) -> LLMProvider:
    """Провайдер на конкретном ключе. SDK-клиент переиспользуется в пределах
    одного ключа, поэтому соединения не пересоздаются на каждое сообщение."""
    if not api_key:
        raise ValueError("Нужен свой API-ключ — пришлите его командой /apikey")

    cache_key = (name, hashlib.sha256(api_key.encode()).hexdigest())
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    builder = _BUILDERS.get(name)
    if builder is None:
        if name in PLANNED:
            raise NotImplementedError(
                f"Провайдер '{name}' ещё не реализован ({PLANNED[name]} roadmap)"
            )
        raise ValueError(f"Неизвестный провайдер: '{name}'")

    if len(_cache) >= _MAX_CACHED:
        _cache.clear()

    provider = builder(cfg, api_key)
    _cache[cache_key] = provider
    return provider


def reset_cache() -> None:
    _cache.clear()
