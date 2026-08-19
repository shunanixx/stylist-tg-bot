"""GeminiProvider: учёт thinking-токенов как output и расход при пустом ответе.

Сеть не трогаем — services.llm.gemini_provider.genai.Client подменяется
фейком, который отдаёт заранее собранный response/исключение.
"""

import pytest

from services.llm.base import LLMError
from services.llm.gemini_provider import GeminiProvider


class FakeUsage:
    def __init__(self, prompt=0, candidates=0, thoughts=0):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.thoughts_token_count = thoughts


class FakeCandidate:
    def __init__(self, finish_reason=None):
        self.finish_reason = finish_reason


class FakeFeedback:
    def __init__(self, block_reason=None):
        self.block_reason = block_reason


class FakeResponse:
    def __init__(self, text, usage, candidates=None, block_reason=None):
        self.text = text
        self.usage_metadata = usage
        self.candidates = candidates or []
        self.prompt_feedback = FakeFeedback(block_reason)


class FakeModels:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def generate_content(self, **kwargs):
        if self._exc:
            raise self._exc
        return self._response


class FakeClient:
    def __init__(self, models):
        self.aio = type("Aio", (), {"models": models})()


@pytest.fixture
def make_provider(monkeypatch):
    def _make(response=None, exc=None):
        client = FakeClient(FakeModels(response=response, exc=exc))
        monkeypatch.setattr(
            "services.llm.gemini_provider.genai.Client", lambda api_key: client
        )
        return GeminiProvider(api_key="fake-key", model="gemini-3.6-flash")

    return _make


async def test_successful_response_bills_thinking_tokens_as_output(make_provider):
    usage = FakeUsage(prompt=1200, candidates=500, thoughts=275)
    provider = make_provider(response=FakeResponse("готовый разбор", usage))

    result = await provider.analyze("system", "текст")

    assert result.raw_text == "готовый разбор"
    assert result.tokens_input == 1200
    assert result.tokens_output == 775  # candidates + thoughts


async def test_max_tokens_with_empty_text_still_reports_the_real_spend(make_provider):
    """Раньше LLMError на пустом тексте не нёс usage: реальный расход —
    промпт плюс thinking, съевший весь MAX_TOKENS, — нигде не логировался."""
    usage = FakeUsage(prompt=1200, candidates=0, thoughts=2048)
    provider = make_provider(
        response=FakeResponse(
            "", usage, candidates=[FakeCandidate(finish_reason="MAX_TOKENS")]
        )
    )

    with pytest.raises(LLMError) as exc_info:
        await provider.analyze("system", "текст")

    error = exc_info.value
    assert error.tokens_input == 1200
    assert error.tokens_output == 2048
    assert "MAX_TOKENS" in str(error)


async def test_blocked_request_names_the_block_reason(make_provider):
    usage = FakeUsage()
    provider = make_provider(response=FakeResponse("", usage, block_reason="SAFETY"))

    with pytest.raises(LLMError, match="Запрос заблокирован"):
        await provider.analyze("system", "текст")


async def test_sdk_exception_is_wrapped_without_fabricating_token_usage(make_provider):
    provider = make_provider(exc=RuntimeError("boom"))

    with pytest.raises(LLMError) as exc_info:
        await provider.analyze("system", "текст")

    assert exc_info.value.tokens_input is None
    assert exc_info.value.tokens_output is None


async def test_empty_request_is_rejected_before_any_api_call(make_provider):
    provider = make_provider()

    with pytest.raises(LLMError, match="Пустой запрос"):
        await provider.analyze("system", "", images=None)
