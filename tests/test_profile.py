"""Онбординг замеров: полный обхват в вопросах, чистка шагов, сводка в конце."""

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from db.crud import users as users_crud
from db.database import Database
from handlers.profile import cmd_profile, cmd_setup, process_measurement

USER_ID = 5150
CHAT_ID = 5150

# Ответы по порядку ORDER: рост, вес, плечи, грудь, талия, пояс
ANSWERS = ["180", "75", "46", "100", "82", "90"]


class FakeBot:
    def __init__(self):
        self.deleted: list[int] = []

    async def delete_message(self, chat_id: int, message_id: int):
        self.deleted.append(message_id)


class Sent:
    def __init__(self, message_id: int, text: str):
        self.message_id = message_id
        self.text = text


class Dialog:
    """Один чат: считает id, помнит отправленное ботом и удалённое."""

    def __init__(self):
        self.bot = FakeBot()
        self.sent: list[Sent] = []
        self.deleted_by_user: list[int] = []
        self._counter = 100

    def _take_id(self) -> int:
        self._counter += 1
        return self._counter

    def user_says(self, text: str) -> "FakeMessage":
        return FakeMessage(text, self._take_id(), self)

    @property
    def texts(self) -> list[str]:
        return [item.text for item in self.sent]


class FakeMessage:
    def __init__(self, text: str, message_id: int, dialog: Dialog):
        self.text = text
        self.message_id = message_id
        self.dialog = dialog
        self.bot = dialog.bot
        self.from_user = type("U", (), {"id": USER_ID})()
        self.chat = type("C", (), {"id": CHAT_ID})()

    async def answer(self, text: str, **kwargs):
        sent = Sent(self.dialog._take_id(), text)
        self.dialog.sent.append(sent)
        return sent

    async def delete(self):
        self.dialog.deleted_by_user.append(self.message_id)


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


@pytest.fixture
def state():
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=CHAT_ID, user_id=USER_ID),
    )


@pytest.fixture
def dialog():
    return Dialog()


async def _walk(dialog, session, state, answers) -> None:
    await cmd_setup(dialog.user_says("/setup"), state)
    for answer in answers:
        await process_measurement(dialog.user_says(answer), session, state)


async def test_girth_questions_ask_for_full_girth(dialog, session, state):
    """Полуобхват вместо обхвата — ошибка вдвое в пункте 5, поэтому в вопросе
    прямо сказано, какое число нужно."""
    await _walk(dialog, session, state, ANSWERS[:3])

    chest_question = dialog.texts[-1]
    assert "полный обхват" in chest_question.lower()
    assert "вокруг тела" in chest_question


async def test_setup_intro_demands_full_girth(dialog, state):
    await cmd_setup(dialog.user_says("/setup"), state)

    assert "полные" in dialog.texts[0].lower()


async def test_each_step_removes_answer_and_previous_question(dialog, session, state):
    """В чате остаётся один живой вопрос: и просьба, и ответ на неё уходят."""
    await cmd_setup(dialog.user_says("/setup"), state)
    intro_id = dialog.sent[0].message_id

    answer = dialog.user_says("180")
    await process_measurement(answer, session, state)

    assert dialog.deleted_by_user == [answer.message_id], "ответ остался в чате"
    assert intro_id in dialog.bot.deleted, "прошлый вопрос остался в чате"
    assert len(dialog.texts) == 2, "новый вопрос должен прийти отдельным сообщением"


async def test_walkthrough_ends_with_the_full_summary(dialog, session, state):
    await _walk(dialog, session, state, ANSWERS)

    summary = dialog.texts[-1]
    assert "Параметры фигуры" in summary
    assert "Рост 180 см" in summary
    assert "Вес 75 кг" in summary
    assert "Ширина плеч 46 см" in summary
    # обхваты — с посчитанным полуобхватом
    assert "Обхват груди 100 см (полуобхват 50)" in summary
    assert "Обхват талии 82 см (полуобхват 41)" in summary
    assert "Обхват пояса 90 см (полуобхват 45)" in summary
    assert await state.get_state() is None


async def test_last_question_is_removed_before_the_summary(dialog, session, state):
    """Иначе сводка приходит под висящим вопросом про пояс."""
    await _walk(dialog, session, state, ANSWERS)

    belt_question_id = dialog.sent[-2].message_id
    assert belt_question_id in dialog.bot.deleted


async def test_summary_marks_the_user_onboarded(dialog, session, state):
    await _walk(dialog, session, state, ANSWERS)

    user = await users_crud.get_user(session, USER_ID)
    assert user.onboarded


async def test_half_girth_is_stored_as_full(dialog, session, state):
    await _walk(dialog, session, state, ["180", "75", "46", "пол 48", "82", "90"])

    assert "Обхват груди 96 см (полуобхват 48)" in dialog.texts[-1]


async def test_skipped_measurement_shows_a_dash(dialog, session, state):
    await _walk(dialog, session, state, ["180", "75", "-", "100", "82", "90"])

    assert "Ширина плеч: —" in dialog.texts[-1]


async def test_wrong_answer_replaces_the_question_instead_of_piling_up(
    dialog, session, state
):
    await cmd_setup(dialog.user_says("/setup"), state)
    first_id = dialog.sent[0].message_id

    bad = dialog.user_says("сто восемьдесят")
    await process_measurement(bad, session, state)

    assert bad.message_id in dialog.deleted_by_user
    assert first_id in dialog.bot.deleted
    repeated = dialog.texts[-1]
    assert "⚠️" in repeated
    assert "Рост" in repeated, "вопрос обязан повториться вместе с замечанием"


async def test_half_on_shoulders_is_rejected_with_a_reason(dialog, session, state):
    await _walk(dialog, session, state, ["180", "75"])

    await process_measurement(dialog.user_says("пол 23"), session, state)

    assert "не обхват" in dialog.texts[-1]


async def test_out_of_range_answer_names_the_full_girth_bounds(dialog, session, state):
    await _walk(dialog, session, state, ["180", "75", "46"])

    await process_measurement(dialog.user_says("48"), session, state)

    complaint = dialog.texts[-1]
    assert "полный обхват" in complaint
    assert "пол 48" in complaint


async def test_profile_command_lists_saved_measurements(dialog, session, state):
    await _walk(dialog, session, state, ANSWERS)

    await cmd_profile(dialog.user_says("/profile"), session, state)

    shown = dialog.texts[-1]
    assert "Рост 180 см" in shown
    assert "/setup" in shown


async def test_profile_of_a_new_user_is_all_dashes(dialog, session, state):
    await cmd_profile(dialog.user_says("/profile"), session, state)

    shown = dialog.texts[-1]
    assert shown.count("—") >= 6
