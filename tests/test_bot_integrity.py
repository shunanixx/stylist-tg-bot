"""Целостность бота: каждая команда и кнопка доходит до своего хендлера.

Тест собирает настоящий Dispatcher из `bot.build_dispatcher` — тот же порядок
роутеров и те же middleware, что в проде, — и скармливает ему настоящие
Update. Никаких вызовов хендлеров напрямую: половина поломок живёт как раз
в проводке (порядок роутеров, фильтры, зависимости хендлера, которых нет
в workflow_data), и прямой вызов их не видит.

Сеть не задействована: сессия бота подменена, а провайдер модели — фейк.
Диспетчер в модуле один: роутеры — модульные объекты, второй include их же
падает «already attached».
"""

import asyncio
import io
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ParseMode
from aiogram.methods import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    File,
    Message,
    PhotoSize,
    Update,
    User,
)
from PIL import Image
from sqlalchemy import delete

from bot import COMMANDS, build_dispatcher
from db.crud import chat_log as chat_log_crud
from db.crud import styles as styles_crud
from db.crud import submissions as submissions_crud
from db.crud import users as users_crud
from db.crud import wardrobe as wardrobe_crud
from db.crud import wishlist as wishlist_crud
from db.database import Database
from db.models import Base
from keyboards import list_kb
from keyboards.menu_kb import BUTTON_TEXTS
from middlewares.track_sent import TrackSentMessagesMiddleware
from services.llm.base import LLMResponse

USER_ID = 424242
OWNER_ID = 1  # conftest выставляет OWNER_USER_ID=1
CHAT_ID = USER_ID

FULL_ANSWER = (
    "1. СТИЛЬ: streetwear\n"
    "16. ВЕРДИКТ: **БРАТЬ**\n"
    '===DATA===\n{"title": "Куртка Carhartt", "verdict": "брать", '
    '"category": "верхняя одежда"}'
)


class FakeSession(BaseSession):
    """Сессия без сети. `make_request` отдаёт уже развёрнутый результат —
    именно так делает AiohttpSession, и на этом ловится ошибка в middleware,
    которая ждёт Response."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, TelegramMethod]] = []
        self.sent_ids: list[int] = []
        self._message_id = 1000

    async def close(self) -> None:
        return None

    async def make_request(self, bot, method, timeout=None):
        name = type(method).__name__
        self.calls.append((name, method))
        if name == "GetFile":
            return File(file_id="f", file_unique_id="u", file_path="photos/f.jpg")
        if name.startswith(("SendMessage", "EditMessageText", "SendPhoto", "Copy")):
            return self._message(getattr(method, "text", None) or "", bot)
        if name == "EditMessageReplyMarkup":
            return self._message("", bot)
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, **kw):
        yield _jpeg_bytes()

    def _message(self, text: str, bot) -> Message:
        """`as_(bot)` обязателен: настоящая сессия монтирует бота в ответ, и без
        этого `status.delete()` в хендлере падает «not mounted to any bot»."""
        self._message_id += 1
        self.sent_ids.append(self._message_id)
        return Message(
            message_id=self._message_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=CHAT_ID, type="private"),
            text=text or None,
        ).as_(bot)

    @property
    def deleted_ids(self) -> list[int]:
        ids: list[int] = []
        for name, method in self.calls:
            if name == "DeleteMessage":
                ids.append(method.message_id)
            elif name == "DeleteMessages":
                ids.extend(method.message_ids)
        return ids

    @property
    def texts(self) -> list[str]:
        return [m.text for name, m in self.calls if name == "SendMessage"]

    @property
    def method_names(self) -> list[str]:
        return [name for name, _ in self.calls]


def _jpeg_bytes(width: int = 1400, height: int = 1000) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(buf, format="JPEG")
    return buf.getvalue()


class FakeLLMRouter:
    """Вместо настоящего Gemini: тест не должен ходить в сеть и жечь квоту."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.images: list[list[bytes] | None] = []

    async def analyze_single(
        self, provider_name, system_prompt, user_text, images=None, api_key=""
    ) -> LLMResponse:
        self.calls.append((provider_name, user_text))
        self.images.append(images)
        return LLMResponse(
            raw_text=FULL_ANSWER, tokens_input=900, tokens_output=400, latency_ms=1200
        )


# Диспетчер и бот — по одному на модуль: include_routers дважды по тем же
# роутерам падает «already attached», а роутеры у нас модульные объекты.
_database = Database("sqlite+aiosqlite:///:memory:")
_dispatcher = build_dispatcher(_database)
_llm = FakeLLMRouter()
_dispatcher["llm_router"] = _llm
_session = FakeSession()
_bot = Bot(
    token="123456789:AAHfake-token-for-integrity-tests",
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    session=_session,
)
_bot.session.middleware(TrackSentMessagesMiddleware())


@pytest_asyncio.fixture(autouse=True)
async def clean_state():
    await _database.create_schema()  # checkfirst=True — вызов повторный безопасен
    async with _database.session() as session:
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(delete(table))
    _session.calls.clear()
    _session.sent_ids.clear()
    _llm.calls.clear()
    _llm.images.clear()
    storage = getattr(_dispatcher.fsm.storage, "storage", None)
    if storage is not None:
        storage.clear()
    yield


def _user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Тест", username="tester")


def _message(text: str, user_id: int, message_id: int = 1) -> Message:
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=_user(user_id),
        text=text,
    )


async def _send(text: str, user_id: int = USER_ID) -> Any:
    """Апдейт как из Telegram, через настоящую проводку диспетчера."""
    return await _dispatcher.feed_update(
        _bot, Update(update_id=_next_update_id(), message=_message(text, user_id))
    )


async def _send_photo(
    user_id: int = USER_ID, caption: str | None = None, media_group_id: str | None = None
) -> Any:
    message = Message(
        message_id=_next_update_id(),
        date=datetime.now(timezone.utc),
        chat=Chat(id=user_id, type="private"),
        from_user=_user(user_id),
        caption=caption,
        media_group_id=media_group_id,
        photo=[
            PhotoSize(file_id="small", file_unique_id="s", width=90, height=67),
            PhotoSize(file_id="big", file_unique_id="b", width=1280, height=960),
        ],
    )
    return await _dispatcher.feed_update(
        _bot, Update(update_id=_next_update_id(), message=message)
    )


async def _press(data: str, user_id: int = USER_ID) -> Any:
    callback = CallbackQuery(
        id=f"cb-{_next_update_id()}",
        from_user=_user(user_id),
        chat_instance="chat-instance",
        data=data,
        # Сообщение под кнопкой отправлено ботом: from_user там — бот
        message=Message(
            message_id=900,
            date=datetime.now(timezone.utc),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=777000, is_bot=True, first_name="Стилист"),
            text="разбор",
        ),
    )
    return await _dispatcher.feed_update(
        _bot, Update(update_id=_next_update_id(), callback_query=callback)
    )


_update_id = 0


def _next_update_id() -> int:
    global _update_id
    _update_id += 1
    return _update_id


async def _give_key(user_id: int = USER_ID) -> None:
    """Без ключа половина путей обрывается раньше хендлера — это отдельный тест."""
    async with _database.session() as session:
        await users_crud.set_api_key(
            session, user_id, _dispatcher["vault"].encrypt("AIza-fake-test-key")
        )


# --- команды и кнопки -------------------------------------------------------


@pytest.mark.parametrize("command", [c.command for c in COMMANDS])
async def test_every_command_in_the_menu_answers(command):
    """Меню команд Telegram обещает пользователю рабочую команду."""
    result = await _send(f"/{command}")

    assert result is not UNHANDLED, f"/{command} не дошла ни до одного хендлера"
    assert _session.texts, f"/{command} не ответила ничего"


@pytest.mark.parametrize("button", sorted(BUTTON_TEXTS))
async def test_every_menu_button_answers(button):
    """Reply-кнопка приходит обычным текстом: её легко перехватить чужим F.text."""
    result = await _send(button)

    assert result is not UNHANDLED, f"кнопка «{button}» не дошла до хендлера"
    assert _session.texts, f"кнопка «{button}» не ответила ничего"


@pytest.mark.parametrize(
    "command", ["cancel", "setup", "apikey_off", "style_add x", "add x", "wish x", "show 1"]
)
async def test_commands_outside_the_menu_also_answer(command):
    """Их нет в COMMANDS, но они обещаны в /help."""
    result = await _send(f"/{command}")

    assert result is not UNHANDLED, f"/{command} не дошла до хендлера"
    assert _session.texts, f"/{command} не ответила ничего"


async def test_unknown_command_is_not_taken_for_an_item():
    """«/foo» не должно уехать в модель как описание вещи."""
    result = await _send("/foo")

    assert result is UNHANDLED
    assert _llm.calls == []


# --- владельческие команды --------------------------------------------------


@pytest.mark.parametrize("command", ["stats", "numbers"])
async def test_admin_commands_are_silent_for_a_regular_user(command):
    result = await _send(f"/{command}")

    assert result is UNHANDLED, f"/{command} ответила не владельцу"
    assert _session.texts == []


@pytest.mark.parametrize("command", ["stats", "numbers"])
async def test_admin_commands_work_for_the_owner(command):
    result = await _send(f"/{command}", user_id=OWNER_ID)

    assert result is not UNHANDLED, f"/{command} не работает у владельца"
    assert _session.texts


# --- разбор вещи ------------------------------------------------------------


async def test_text_analysis_reaches_the_model():
    await _give_key()

    await _send("Куртка Carhartt Detroit, размер M, б/у, 2500 грн")

    assert len(_llm.calls) == 1
    assert _llm.images == [None]
    assert any("БРАТЬ" in (text or "") for text in _session.texts)


async def test_photo_analysis_reaches_the_model():
    await _give_key()

    await _send_photo(caption="джинсы, 40 см по поясу")

    assert len(_llm.calls) == 1
    assert _llm.calls[0][1] == "джинсы, 40 см по поясу"
    assert _llm.images[0] and len(_llm.images[0]) == 1
    # Максимальный PhotoSize, а не превью 90 px
    assert ("GetFile", "big") in [
        (name, getattr(method, "file_id", None)) for name, method in _session.calls
    ]


async def test_album_goes_to_the_model_as_one_request(monkeypatch):
    """Альбом Telegram доставляет по одному фото — в модель должен уехать один
    запрос, а не три."""
    from handlers import analysis

    monkeypatch.setattr(analysis._media_buffer, "_settle_delay", 0.01)
    await _give_key()

    await asyncio.gather(
        _send_photo(media_group_id="album-1", caption="лоты с барахолки"),
        _send_photo(media_group_id="album-1"),
        _send_photo(media_group_id="album-1"),
    )

    assert len(_llm.calls) == 1
    assert len(_llm.images[0]) == 3
    assert _llm.calls[0][1] == "лоты с барахолки"


async def test_analysis_without_a_key_says_so_instead_of_calling_the_model():
    await _send("Куртка Carhartt Detroit, размер M, б/у, 2500 грн")

    assert _llm.calls == []
    assert any("ключ" in (text or "").lower() for text in _session.texts)


async def test_buttons_under_the_analysis_lead_to_wardrobe_and_wishlist():
    await _give_key()
    await _send("Куртка Carhartt Detroit, размер M, б/у, 2500 грн")
    submission_id = await _last_submission_id()

    assert await _press(f"wardrobe:add:{submission_id}") is not UNHANDLED
    assert await _press(f"wishlist:add:{submission_id}") is not UNHANDLED

    async with _database.session() as session:
        assert len(await wardrobe_crud.list_items(session, USER_ID)) == 1
        assert len(await wishlist_crud.list_items(session, USER_ID)) == 1


async def _last_submission_id() -> int:
    async with _database.session() as session:
        recent = await submissions_crud.recent_submissions(session, USER_ID, 1)
    assert recent, "разбор не сохранился"
    return recent[0].id


# --- инлайн-кнопки списков --------------------------------------------------

_LIST_ACTIONS = [
    (list_kb.STYLES, list_kb.ADD),
    (list_kb.STYLES, list_kb.RENAME),
    (list_kb.STYLES, list_kb.DELETE),
    (list_kb.STYLES, list_kb.CANCEL),
    (list_kb.WARDROBE, list_kb.ADD),
    (list_kb.WARDROBE, list_kb.DELETE),
    (list_kb.WARDROBE, list_kb.CANCEL),
    (list_kb.WISHLIST, list_kb.ADD),
    (list_kb.WISHLIST, list_kb.BOUGHT),
    (list_kb.WISHLIST, list_kb.DELETE),
    (list_kb.WISHLIST, list_kb.CANCEL),
]


@pytest.mark.parametrize(("section", "action"), _LIST_ACTIONS)
async def test_every_list_button_reaches_a_handler(section, action):
    data = list_kb.callback_data(section, action)

    result = await _press(data)

    assert result is not UNHANDLED, f"кнопка {data} никуда не ведёт"


@pytest.mark.parametrize(
    ("section", "text", "lister"),
    [
        (list_kb.WARDROBE, "Худи серое", "wardrobe"),
        (list_kb.WISHLIST, "Ботинки Solovair", "wishlist"),
        (list_kb.STYLES, "минимализм", "styles"),
    ],
)
async def test_button_then_plain_text_adds_the_item(section, text, lister):
    """Кнопка спрашивает название, ответ приходит обычным текстом — путь
    проходит через FSM и не должен доставаться analysis.router."""
    await _press(list_kb.callback_data(section, list_kb.ADD))

    await _send(text)

    listers = {
        "wardrobe": wardrobe_crud.list_items,
        "wishlist": wishlist_crud.list_items,
        "styles": styles_crud.list_styles,
    }
    async with _database.session() as session:
        items = await listers[lister](session, USER_ID)
    assert len(items) == 1, f"«{text}» не добавилось в {lister}"
    assert _llm.calls == [], "ответ на вопрос уехал в модель как описание вещи"


async def test_item_button_deletes_exactly_that_item():
    await _press(list_kb.callback_data(list_kb.WARDROBE, list_kb.ADD))
    await _send("Худи серое")

    async with _database.session() as session:
        item_id = (await wardrobe_crud.list_items(session, USER_ID))[0].id

    result = await _press(
        list_kb.callback_data(list_kb.WARDROBE, list_kb.DELETE, item_id)
    )

    assert result is not UNHANDLED
    async with _database.session() as session:
        assert await wardrobe_crud.list_items(session, USER_ID) == []


# --- FSM онбординга ---------------------------------------------------------


async def test_setup_walks_through_the_questions():
    await _send("/setup")

    await _send("180")
    await _send("78")

    async with _database.session() as session:
        user = await users_crud.get_user(session, USER_ID)
    assert (user.height_cm, user.weight_kg) == (180, 78)
    assert _llm.calls == [], "ответ на вопрос замера уехал в модель"


async def test_menu_button_interrupts_an_open_dialog():
    """Иначе подпись кнопки ушла бы в FSM как ответ на вопрос."""
    await _send("/setup")

    await _send("🧥 Гардероб")

    assert any("прерван" in (text or "") for text in _session.texts)
    async with _database.session() as session:
        user = await users_crud.get_user(session, USER_ID)
    assert user.height_cm is None


# --- журнал чата и /clear ---------------------------------------------------


async def test_bot_own_messages_land_in_the_chat_log():
    """/clear удаляет по id из журнала: если исходящие туда не попадают,
    команда «работает», но ответы бота остаются в чате."""
    await _send("/help")

    async with _database.session() as session:
        tracked = await chat_log_crud.list_ids(session, USER_ID, CHAT_ID)
    missing = set(_session.sent_ids) - set(tracked)
    assert not missing, f"ответы бота не попали в журнал: {sorted(missing)}"


async def test_clear_deletes_both_sides_of_the_conversation():
    await _send("/help")
    bot_message_ids = list(_session.sent_ids)
    await _send("/clear")

    result = await _press("clear:yes")

    assert result is not UNHANDLED
    assert set(bot_message_ids) <= set(_session.deleted_ids), (
        "ответы бота остались в чате после /clear"
    )

    # В журнале остаётся только свежий отчёт «убрал N сообщ.» — его тоже надо
    # будет когда-то вычистить, поэтому он там законно.
    async with _database.session() as session:
        left = await chat_log_crud.list_ids(session, USER_ID, CHAT_ID)
    assert set(left) & set(bot_message_ids) == set(), "удалённое осталось в журнале"


# --- /forget ----------------------------------------------------------------


async def test_forget_leaves_no_row_behind():
    """Журнал чата пишется после хендлера — и легко воскрешает удалённого."""
    await _send("/start")
    await _send("/forget да")

    async with _database.session() as session:
        assert await users_crud.get_user(session, USER_ID) is None


async def test_forget_needs_confirmation():
    await _send("/start")

    await _send("/forget")

    async with _database.session() as session:
        assert await users_crud.get_user(session, USER_ID) is not None
