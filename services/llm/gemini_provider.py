import time

from google import genai
from google.genai import types

from services.llm.base import LLMError, LLMProvider, LLMResponse


class GeminiProvider(LLMProvider):
    """Основной провайдер проекта. Vision, system_instruction, usage_metadata."""

    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def analyze(
        self,
        system_prompt: str,
        user_text: str,
        images: list[bytes] | None = None,
    ) -> LLMResponse:
        parts: list[types.Part] = [
            types.Part.from_bytes(data=image, mime_type="image/jpeg")
            for image in images or []
        ]
        if user_text:
            parts.append(types.Part.from_text(text=user_text))
        if not parts:
            raise LLMError(self.name, "Пустой запрос: нет ни текста, ни изображений")

        started = time.perf_counter()
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                config=types.GenerateContentConfig(system_instruction=system_prompt),
                contents=parts,
            )
        except Exception as exc:  # SDK бросает свои типы ошибок
            raise LLMError(self.name, f"{type(exc).__name__}: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = response.usage_metadata
        tokens_input = getattr(usage, "prompt_token_count", None) or 0
        # thinking-токены тарифицируются как output, поэтому учитываем их тоже
        tokens_output = (getattr(usage, "candidates_token_count", None) or 0) + (
            getattr(usage, "thoughts_token_count", None) or 0
        )

        text = (response.text or "").strip()
        if not text:
            # Google уже списал tokens_input/tokens_output (типичный случай —
            # MAX_TOKENS съеденный thinking'ом) даже без видимого текста ответа.
            # Не приложить их к ошибке значило бы потерять реальный расход из БД.
            raise LLMError(
                self.name,
                self._describe_empty(response),
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                latency_ms=latency_ms,
            )

        return LLMResponse(
            raw_text=text,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            latency_ms=latency_ms,
        )

    async def count_tokens(self, text_or_parts: object) -> int:
        """Оценка размера запроса до отправки — для контроля бюджета (раздел 6.4)."""
        result = await self._client.aio.models.count_tokens(
            model=self._model, contents=text_or_parts
        )
        return result.total_tokens or 0

    def _describe_empty(self, response: object) -> str:
        feedback = getattr(response, "prompt_feedback", None)
        block_reason = getattr(feedback, "block_reason", None)
        if block_reason:
            return f"Запрос заблокирован: {block_reason}"
        candidates = getattr(response, "candidates", None) or []
        finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        if finish_reason:
            return (
                f"Пустой ответ, finish_reason={finish_reason}. "
                "Если MAX_TOKENS — лимит съеден thinking-токенами"
            )
        return "Пустой ответ модели"
