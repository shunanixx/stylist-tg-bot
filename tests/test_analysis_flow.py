"""End-to-end проверка хендлера анализа с подставным провайдером."""

import pytest
import pytest_asyncio

from db.crud import submissions as submissions_crud
from db.crud import users as users_crud
from db.crud import wardrobe as wardrobe_crud
from db.database import Database
from handlers.analysis import handle_text_analysis
from services.llm.base import LLMError, LLMResponse
from services.prompt_builder import PromptBuilder

USER_ID = 555


class FakeMessage:
    def __init__(self, text: str, user_id: int = USER_ID):
        self.text = text
        self.from_user = type("U", (), {"id": user_id})()
        self.sent: list[tuple[str, object]] = []
        self.status_edits: list[str] = []
        self.status_deleted = False

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.sent.append((text, reply_markup))
        return _Status(self)


class _Status:
    def __init__(self, parent: FakeMessage):
        self._parent = parent

    async def edit_text(self, text: str, **kwargs):
        self._parent.status_edits.append(text)

    async def delete(self):
        self._parent.status_deleted = True


class FakeRouter:
    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.system_prompts: list[str] = []
        self.images: list[list[bytes]] = []
        self.api_keys: list[str] = []

    async def analyze_single(
        self, provider_name, system_prompt, user_text, images=None, api_key=None
    ):
        self.system_prompts.append(system_prompt)
        self.api_keys.append(api_key)
        if images:
            self.images.append(list(images))
        if self._error:
            raise self._error
        return self._response


FULL_ANSWER = (
    "1. СТИЛЬ: streetwear\n"
    "16. ВЕРДИКТ: **БРАТЬ**\n"
    '===DATA===\n{"title": "Куртка Carhartt", "verdict": "брать", '
    '"category": "верхняя одежда"}'
)


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


@pytest.fixture
def builder():
    return PromptBuilder()


@pytest_asyncio.fixture
async def keyed_session(session, encrypted_key):
    """Бот публичный: без своего ключа анализ до модели не доходит."""
    await users_crud.set_api_key(session, USER_ID, encrypted_key)
    return session


async def test_successful_analysis_persists_and_replies(keyed_session, builder, vault):
    await users_crud.update_measurements(keyed_session, USER_ID, height_cm=175, belt_cm=42.5)
    await wardrobe_crud.add_item(keyed_session, USER_ID, "Серый свитшот", size="S")
    message = FakeMessage("Куртка Carhartt Detroit, M, 2500 грн")
    llm = FakeRouter(
        LLMResponse(raw_text=FULL_ANSWER, tokens_input=1500, tokens_output=800, latency_ms=4200)
    )

    await handle_text_analysis(message, keyed_session, builder, llm, vault)

    # промпт собран из актуальных данных
    prompt = llm.system_prompts[0]
    assert "Рост 175 см" in prompt
    assert "- Серый свитшот (S)" in prompt

    # пользователю ушёл разбор без служебного блока и с футером
    final_text, markup = message.sent[-1]
    assert "===DATA===" not in final_text
    assert "1. СТИЛЬ" in final_text
    assert "gemini · 1500→800 ток. · 4.2 с · вердикт: брать" in final_text
    assert markup is not None
    assert message.status_deleted

    # запись в БД
    recent = await submissions_crud.recent_submissions(keyed_session, USER_ID)
    assert [s.item_title for s in recent] == ["Куртка Carhartt"]
    assert recent[0].final_verdict == "брать"
    assert recent[0].item_category == "верхняя одежда"

    results = await submissions_crud.results_for(keyed_session, recent[0].id)
    assert results[0].provider == "gemini"
    assert results[0].tokens_input == 1500
    assert results[0].raw_response == FULL_ANSWER
    assert "===DATA===" not in results[0].full_response


async def test_own_key_reaches_router(keyed_session, builder, vault):
    """Расход должен идти с ключа пользователя, а не с ключа владельца."""
    from tests.conftest import TEST_API_KEY

    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await handle_text_analysis(FakeMessage("Куртка Carhartt, M"), keyed_session, builder, llm, vault)

    assert llm.api_keys == [TEST_API_KEY]


async def test_analysis_without_key_stops_before_model(session, builder, vault):
    """У нового пользователя ключа нет — до модели дойти не должно."""
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))
    message = FakeMessage("Куртка Carhartt, M")

    await handle_text_analysis(message, session, builder, llm, vault)

    assert llm.system_prompts == []
    assert "/apikey" in message.sent[0][0]
    assert await submissions_crud.recent_submissions(session, USER_ID) == []


async def test_previous_verdicts_feed_next_prompt(keyed_session, builder, vault):
    submission = await submissions_crud.create_submission(keyed_session, USER_ID, "text", "Кеды")
    await submissions_crud.set_item_meta(
        keyed_session, submission.id, USER_ID, "Кеды Puma", "обувь", "не брать"
    )
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await handle_text_analysis(
        FakeMessage("Худи Champion, L"), keyed_session, builder, llm, vault
    )

    assert "- Кеды Puma: не брать" in llm.system_prompts[0]


async def test_provider_error_reports_without_writing_db(keyed_session, builder, vault):
    message = FakeMessage("Куртка Carhartt, M")
    llm = FakeRouter(error=LLMError("gemini", "503 unavailable"))

    await handle_text_analysis(message, keyed_session, builder, llm, vault)

    assert message.status_edits == ["Провайдер не ответил. Попробуй ещё раз через минуту."]
    assert await submissions_crud.recent_submissions(keyed_session, USER_ID) == []


async def test_error_with_reported_usage_still_logs_the_real_spend(
    keyed_session, builder, vault
):
    """MAX_TOKENS съеденный thinking'ом даёт пустой текст и LLMError, но Google
    уже списал tokens_input/tokens_output — их нужно сохранить, а не потерять."""
    from sqlalchemy import select

    from db.models import Submission

    message = FakeMessage("Куртка Carhartt, M")
    llm = FakeRouter(
        error=LLMError(
            "gemini",
            "Пустой ответ, finish_reason=MAX_TOKENS",
            tokens_input=1200,
            tokens_output=2048,
        )
    )

    await handle_text_analysis(message, keyed_session, builder, llm, vault)

    # В /history такой разбор не всплывает: item_title не проставлен
    assert await submissions_crud.recent_submissions(keyed_session, USER_ID) == []

    submission = (
        await keyed_session.scalars(
            select(Submission).where(Submission.user_id == USER_ID)
        )
    ).one()
    results = await submissions_crud.results_for(keyed_session, submission.id)
    assert len(results) == 1
    assert results[0].tokens_input == 1200
    assert results[0].tokens_output == 2048


async def test_disabled_provider_message(keyed_session, builder, vault):
    message = FakeMessage("Куртка Carhartt, M")
    llm = FakeRouter(error=NotImplementedError("Провайдер 'kimi' ещё не реализован"))

    await handle_text_analysis(message, keyed_session, builder, llm, vault)

    assert "недоступен" in message.status_edits[0]


async def test_answer_without_data_block_still_delivered(keyed_session, builder, vault):
    message = FakeMessage("Джинсы Levi's 501, W30")
    llm = FakeRouter(LLMResponse(raw_text="1. СТИЛЬ: denim\n16. ВЕРДИКТ: брать"))

    await handle_text_analysis(message, keyed_session, builder, llm, vault)

    final_text, _ = message.sent[-1]
    assert "1. СТИЛЬ: denim" in final_text
    assert "вердикт: не определено" in final_text
    recent = await submissions_crud.recent_submissions(keyed_session, USER_ID)
    assert recent[0].item_title == "Без названия"


async def test_long_answer_is_split_into_several_messages(keyed_session, builder, vault):
    long_body = "\n\n".join(f"{i}. ПУНКТ {'текст ' * 120}" for i in range(1, 12))
    llm = FakeRouter(LLMResponse(raw_text=f"{long_body}\n===DATA===\n{{\"title\": \"Вещь\"}}"))
    message = FakeMessage("Куртка, M")

    await handle_text_analysis(message, keyed_session, builder, llm, vault)

    analysis_messages = [text for text, _ in message.sent[1:]]
    assert len(analysis_messages) > 1
    assert all(len(chunk) <= 4096 for chunk in analysis_messages)


async def test_too_short_input_is_rejected_before_llm(keyed_session, builder, vault):
    message = FakeMessage("ок")
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await handle_text_analysis(message, keyed_session, builder, llm, vault)

    assert llm.system_prompts == []
    assert "Слишком коротко" in message.sent[0][0]
