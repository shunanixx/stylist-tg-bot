"""Кнопки меню: тот же вход, что у команд, и приоритет над FSM/анализом."""

import pytest
import pytest_asyncio
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import bot as bot_module
from db.crud import styles as styles_crud
from db.crud import wardrobe as wardrobe_crud
from db.crud import users as users_crud
from db.database import Database
from handlers.menu import cmd_menu, press_menu_button
from handlers.profile import cmd_setup
from keyboards.menu_kb import BUTTON_TO_ACTION, MENU_BUTTONS
from states.onboarding_states import Onboarding

USER_ID = 9090


class FakeMessage:
    def __init__(self, text: str, user_id: int = USER_ID):
        self.text = text
        self.from_user = type("U", (), {"id": user_id})()
        self.sent: list[str] = []
        self.markups: list[object] = []

    async def answer(self, text: str, **kwargs):
        self.sent.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return self


class Cfg:
    owner_user_id = 1
    google_api_key = None
    gemini_model = "gemini-3.6-flash"
    default_llm_provider = "gemini"


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


@pytest.fixture
def state():
    storage = MemoryStorage()
    return FSMContext(
        storage=storage,
        key=StorageKey(bot_id=1, chat_id=USER_ID, user_id=USER_ID),
    )


def _press(label: str) -> FakeMessage:
    assert label in BUTTON_TO_ACTION, "подпись кнопки разошлась с таблицей разделов"
    return FakeMessage(label)


async def test_every_button_reaches_its_section(session, state, vault):
    """Ни одна кнопка не должна молчать — это единственный путь к разделу
    для тех, кто командами не пользуется."""
    for label, _ in MENU_BUTTONS:
        message = _press(label)

        await press_menu_button(message, session, state, vault, Cfg())

        assert message.sent, f"кнопка «{label}» не ответила"


async def test_wardrobe_button_shows_the_same_list_as_command(session, state, vault):
    await wardrobe_crud.add_item(session, USER_ID, title="Серый свитшот")
    message = _press("🧥 Гардероб")

    await press_menu_button(message, session, state, vault, Cfg())

    assert "Серый свитшот" in message.sent[0]
    assert "1. " in message.sent[0]


async def test_styles_button_lists_own_styles(session, state, vault):
    await styles_crud.add_style(session, USER_ID, "минимализм")
    message = _press("🎨 Мои стили")

    await press_menu_button(message, session, state, vault, Cfg())

    assert "минимализм" in message.sent[0]


async def test_key_button_does_not_treat_its_label_as_a_key(session, state, vault):
    """«🔑 Ключ» — две «слова»: разбор текста как у /apikey принял бы подпись
    кнопки за присланный ключ и попытался его сохранить."""
    message = _press("🔑 Ключ")

    await press_menu_button(message, session, state, vault, Cfg())

    user = await users_crud.get_user(session, USER_ID)
    assert user is None or user.google_api_key_enc is None
    assert "aistudio.google.com" in message.sent[0]


async def test_button_aborts_unfinished_setup(session, state, vault):
    """Иначе подпись кнопки уедет в FSM как ответ на вопрос о замерах."""
    await cmd_setup(FakeMessage("/setup"), state)
    assert await state.get_state() == Onboarding.height.state

    message = _press("📜 Разборы")
    await press_menu_button(message, session, state, vault, Cfg())

    assert await state.get_state() is None
    assert "прерван" in message.sent[0].lower()


async def test_menu_command_sends_the_keyboard():
    message = FakeMessage("/menu")

    await cmd_menu(message)

    assert message.markups[0] is not None, "/menu обязан вернуть клавиатуру"


def test_every_button_has_a_matching_command():
    """Кнопки не заменяют команды, а дублируют их — расхождение списков
    означало бы раздел, доступный только одним способом."""
    commands = {command.command for command in bot_module.COMMANDS}
    assert set(BUTTON_TO_ACTION.values()) <= commands
