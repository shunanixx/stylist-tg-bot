import asyncio
import logging

from config import Settings
from services.llm.base import LLMResponse
from services.llm.factory import get_provider

logger = logging.getLogger(__name__)


class LLMRouter:
    """Оркестрация одного или пары вызовов. Сравнение — не больше
    MAX_CONCURRENT_AGENTS провайдеров: это контроль стоимости, а не только UI.
    """

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.enabled = list(cfg.enabled_providers)

    async def analyze_single(
        self,
        provider_name: str,
        system_prompt: str,
        user_text: str,
        images: list[bytes] | None = None,
        api_key: str = "",
    ) -> LLMResponse:
        if provider_name not in self.enabled:
            raise ValueError(f"Провайдер '{provider_name}' отключён в конфиге")
        provider = get_provider(provider_name, self.cfg, api_key)
        return await provider.analyze(system_prompt, user_text, images)

    async def analyze_compare(
        self,
        provider_names: list[str],
        system_prompt: str,
        user_text: str,
        images: list[bytes] | None = None,
        api_key: str = "",
    ) -> dict[str, LLMResponse | BaseException]:
        if len(provider_names) > self.cfg.max_concurrent_agents:
            raise ValueError(
                f"Максимум {self.cfg.max_concurrent_agents} агентов одновременно, "
                f"передано {len(provider_names)}"
            )
        for name in provider_names:
            if name not in self.enabled:
                raise ValueError(f"Провайдер '{name}' отключён в конфиге")

        providers = [get_provider(name, self.cfg, api_key) for name in provider_names]
        results = await asyncio.gather(
            *(p.analyze(system_prompt, user_text, images) for p in providers),
            # если один из пары упал — сравнение не падает целиком
            return_exceptions=True,
        )
        for name, result in zip(provider_names, results):
            if isinstance(result, BaseException):
                logger.warning("Провайдер %s вернул ошибку: %s", name, result)
        return dict(zip(provider_names, results))
