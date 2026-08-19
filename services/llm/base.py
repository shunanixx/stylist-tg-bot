from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    raw_text: str
    tokens_input: int = 0
    tokens_output: int = 0
    latency_ms: int = 0


class LLMError(RuntimeError):
    """Ошибка вызова провайдера — сеть, лимиты, блокировка ответа.

    tokens_* — заполняются, только если провайдер успел получить usage_metadata
    до того, как решил, что ответ непригоден (например, Gemini с пустым text
    при finish_reason=MAX_TOKENS всё равно тратит промпт- и thinking-токены).
    Без них этот расход нигде не логируется — см. GeminiProvider.analyze.
    """

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.tokens_input = tokens_input
        self.tokens_output = tokens_output
        self.latency_ms = latency_ms


class LLMProvider(ABC):
    name: str  # 'gemini' | 'deepseek' | 'kimi' | 'claude' | 'openai'

    @abstractmethod
    async def analyze(
        self,
        system_prompt: str,
        user_text: str,
        images: list[bytes] | None = None,
    ) -> LLMResponse: ...
