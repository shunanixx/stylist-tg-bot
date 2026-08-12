import asyncio

import pytest

from services.llm.base import LLMError, LLMProvider, LLMResponse
from services.llm_router import LLMRouter


class FakeProvider(LLMProvider):
    def __init__(self, name: str, delay: float = 0, error: Exception | None = None):
        self.name = name
        self._delay = delay
        self._error = error
        self.calls: list[tuple[str, str, list[bytes] | None]] = []

    async def analyze(self, system_prompt, user_text, images=None):
        self.calls.append((system_prompt, user_text, images))
        await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return LLMResponse(raw_text=f"ответ {self.name}", tokens_input=10, tokens_output=20)


@pytest.fixture
def cfg():
    class Cfg:
        enabled_providers = ["gemini", "deepseek", "kimi"]
        max_concurrent_agents = 2

    return Cfg()


@pytest.fixture
def router(cfg, monkeypatch):
    providers = {
        "gemini": FakeProvider("gemini"),
        "deepseek": FakeProvider("deepseek", delay=0.01),
        "kimi": FakeProvider("kimi", error=LLMError("kimi", "429 rate limit")),
    }
    seen_keys: list[str] = []

    def fake_get_provider(name, _cfg, api_key):
        seen_keys.append(api_key)
        return providers[name]

    monkeypatch.setattr("services.llm_router.get_provider", fake_get_provider)
    instance = LLMRouter(cfg)
    instance.fakes = providers
    instance.seen_keys = seen_keys
    return instance


async def test_analyze_single_passes_prompt_through(router):
    response = await router.analyze_single("gemini", "SYS", "куртка", api_key="k-1")

    assert response.raw_text == "ответ gemini"
    assert router.fakes["gemini"].calls == [("SYS", "куртка", None)]


async def test_api_key_reaches_factory(router):
    """Ключ пользователя должен доехать до фабрики — иначе расход уйдёт чужому."""
    await router.analyze_single("gemini", "SYS", "куртка", api_key="ключ-пользователя")

    assert router.seen_keys == ["ключ-пользователя"]


async def test_analyze_single_rejects_disabled_provider(router):
    with pytest.raises(ValueError, match="отключён"):
        await router.analyze_single("claude", "SYS", "куртка", api_key="k-1")


async def test_analyze_compare_returns_dict_keyed_by_provider(router):
    results = await router.analyze_compare(
        ["gemini", "deepseek"], "SYS", "куртка", api_key="k-1"
    )

    assert set(results) == {"gemini", "deepseek"}
    assert results["deepseek"].raw_text == "ответ deepseek"


async def test_compare_survives_one_failing_provider(router):
    results = await router.analyze_compare(["gemini", "kimi"], "SYS", "куртка", api_key="k-1")

    assert results["gemini"].raw_text == "ответ gemini"
    assert isinstance(results["kimi"], LLMError)


async def test_compare_respects_max_concurrent_agents(router):
    with pytest.raises(ValueError, match="Максимум 2"):
        await router.analyze_compare(
            ["gemini", "deepseek", "kimi"], "SYS", "куртка", api_key="k-1"
        )


async def test_owner_compares_more_agents_than_the_limit(router, cfg):
    """Лимит агентов — защита от чужого расхода; владелец платит своей квотой."""
    cfg.owner_user_id = 42

    results = await router.analyze_compare(
        ["gemini", "deepseek", "kimi"], "SYS", "куртка", api_key="k-1", user_id=42
    )

    assert set(results) == {"gemini", "deepseek", "kimi"}


async def test_other_user_still_hits_the_limit_when_owner_is_set(router, cfg):
    cfg.owner_user_id = 42

    with pytest.raises(ValueError, match="Максимум 2"):
        await router.analyze_compare(
            ["gemini", "deepseek", "kimi"], "SYS", "куртка", api_key="k-1", user_id=7
        )


async def test_compare_rejects_disabled_before_calling(router):
    with pytest.raises(ValueError, match="отключён"):
        await router.analyze_compare(["gemini", "openai"], "SYS", "куртка", api_key="k-1")

    assert router.fakes["gemini"].calls == []
