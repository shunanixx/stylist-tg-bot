from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    raw_text: str
    tokens_input: int = 0
    tokens_output: int = 0
    latency_ms: int = 0


class LLMError(RuntimeError):
    """Ошибка вызова провайдера — сеть, лимиты, блокировка ответа."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider


class LLMProvider(ABC):
    name: str  # 'gemini' | 'deepseek' | 'kimi' | 'claude' | 'openai'

    @abstractmethod
    async def analyze(
        self,
        system_prompt: str,
        user_text: str,
        images: list[bytes] | None = None,
    ) -> LLMResponse: ...
