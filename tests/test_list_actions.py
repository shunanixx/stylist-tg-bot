"""Управление списками кнопками: гардероб, вишлист, стили — без команд.

Проверяем то, что легко разъезжается: нажатие просит текст, текст без команды
доходит до CRUD, кнопка вещи бьёт по id (а не по номеру), устаревшая кнопка
ничего не удаляет, и «menu:*» не пересекается с кнопками разбора.
"""

from types import SimpleNamespace

import pytest_asyncio

from db.crud import styles as styles_crud
from db.crud import wardrobe as wardrobe_crud
from db.crud import wishlist as wishlist_crud
from db.database import Database
from handlers import styles as styles_handlers
from handlers import wardrobe as wardrobe_handlers
from handlers import wishlist as wishlist_handlers
from keyboards import list_kb
from keyboards.analysis_kb import ADD_TO_WARDROBE_PREFIX, ADD_TO_WISHLIST_PREFIX
from states.list_states import StyleInput, WardrobeInput, WishlistInput

USER_ID = 777


class FakeMessage:
    def __init__(self, text: str | None = None, user_id: int = USER_ID):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.sent: list[str] = []
        self.markups: list[object] = []
        self.markup_edits: list[object] = []

    async def answer(self, text: str, **kwargs):
        self.sent.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return SimpleNamespace(message_id=len(self.sent))

    async def edit_reply_markup(self, reply_markup=None):
        self.markup_edits.append(reply_markup)


class FakeCallback:
    """Сообщение под кнопкой отправлено ботом: from_user у него — бот, поэтому
    хендлеры обязаны брать id из самого callback."""

    def __init__(self, data: str, user_id: int = USER_ID):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = FakeMessage(user_id=0)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


class FakeState:
    def __init__(self):
        self.state = None
        self.data: dict = {}

    async def set_state(self, state):
        self.state = state

    async def get_state(self):
        return self.state.state if self.state is not None else None

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.state = None
        self.data = {}


class FakeCommand:
    def __init__(self, args: str | None):
        self.args = args


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


def _buttons(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _matching_handlers(router, data: str) -> list[str]:
    """Какие callback-хендлеры роутера подходят под эти данные."""
    fake = SimpleNamespace(data=data)
    names = []
    for handler in router.callback_query.handlers:
        if all(_passes(one, fake) for one in handler.filters or []):
            names.append(handler.callback.__name__)
    return names


def _passes(filter_object, fake) -> bool:
    # У магического фильтра aiogram кладёт в callback сразу MagicFilter.resolve
    return bool(filter_object.callback(fake))


# --- стили: добавление текстом ------------------------------------------


async def test_add_button_asks_for_plain_text(session):
    callback = FakeCallback(list_kb.callback_data(list_kb.STYLES, list_kb.ADD))
    state = FakeState()

    await styles_handlers.press_add(callback, state)

    assert state.state == StyleInput.name
    prompt = callback.message.sent[0]
    assert "текст" in prompt.lower()
    # Смысл кнопки в том, что команду набирать не надо
    assert "/style_add" not in prompt
    assert list_kb.CANCEL_TEXT in _buttons(callback.message.markups[0])


async def test_typed_text_becomes_a_style(session):
    state = FakeState()
    await styles_handlers.press_add(FakeCallback("x"), state)

    message = FakeMessage("минимализм")
    await styles_handlers.receive_name(message, session, state)

    assert [s.name for s in await styles_crud.list_styles(session, USER_ID)] == [
        "минимализм"
    ]
    assert state.state is None  # ввод закрыт, следующий текст уйдёт в разбор
    assert "1. минимализм" in message.sent[0]
    assert "➕ Добавить" in _buttons(message.markups[0])


async def test_typed_text_still_splits_by_comma(session):
    state = FakeState()
    await styles_handlers.press_add(FakeCallback("x"), state)

    await styles_handlers.receive_name(FakeMessage("casual, old money"), session, state)

    assert [s.name for s in await styles_crud.list_styles(session, USER_ID)] == [
        "casual",
        "old money",
    ]


async def test_command_typed_by_mistake_is_not_saved(session):
    """«/wardrobe» в ответ на вопрос — это уход в раздел, а не название стиля."""
    state = FakeState()
    await styles_handlers.press_add(FakeCallback("x"), state)

    message = FakeMessage("/foo")
    await styles_handlers.receive_name(message, session, state)

    assert await styles_crud.list_styles(session, USER_ID) == []
    assert state.state == StyleInput.name  # вопрос остаётся в силе
    assert "текстом" in message.sent[0]


# --- стили: переименование и удаление кнопками --------------------------


async def test_rename_goes_through_buttons_and_text(session):
    await styles_crud.add_style(session, USER_ID, "gorpcore")
    target = (await styles_crud.list_styles(session, USER_ID))[0]

    picker = FakeCallback(list_kb.callback_data(list_kb.STYLES, list_kb.RENAME))
    await styles_handlers.press_rename(picker, session)
    assert _callbacks(picker.message.markups[0])[0] == list_kb.callback_data(
        list_kb.STYLES, list_kb.RENAME, target.id
    )

    state = FakeState()
    chosen = FakeCallback(
        list_kb.callback_data(list_kb.STYLES, list_kb.RENAME, target.id)
    )
    await styles_handlers.press_rename_target(chosen, session, state)
    assert state.state == StyleInput.new_name
    assert state.data["style_id"] == target.id

    message = FakeMessage("workwear")
    await styles_handlers.receive_new_name(message, session, state)

    assert [s.name for s in await styles_crud.list_styles(session, USER_ID)] == [
        "workwear"
    ]
    assert "gorpcore → workwear" in message.sent[0]


async def test_delete_button_carries_the_id_not_the_position(session):
    """Позиция сдвигается после каждой правки, id — нет: в кнопке именно он."""
    for name in ("первый", "второй", "третий"):
        await styles_crud.add_style(session, USER_ID, name)
    items = await styles_crud.list_styles(session, USER_ID)

    picker = FakeCallback(list_kb.callback_data(list_kb.STYLES, list_kb.DELETE))
    await styles_handlers.press_delete(picker, session)
    assert _callbacks(picker.message.markups[0])[:3] == [
        list_kb.callback_data(list_kb.STYLES, list_kb.DELETE, item.id)
        for item in items
    ]

    chosen = FakeCallback(
        list_kb.callback_data(list_kb.STYLES, list_kb.DELETE, items[1].id)
    )
    await styles_handlers.press_delete_target(chosen, session)

    assert [s.name for s in await styles_crud.list_styles(session, USER_ID)] == [
        "первый",
        "третий",
    ]
    # Обновлённый список уходит в чат, а не только во всплывашку
    assert "1. первый" in chosen.message.sent[0]
    assert "2. третий" in chosen.message.sent[0]


async def test_stale_delete_button_deletes_nothing(session):
    await styles_crud.add_style(session, USER_ID, "gorpcore")
    await styles_crud.add_style(session, USER_ID, "techwear")
    items = await styles_crud.list_styles(session, USER_ID)
    await styles_crud.delete_style(session, USER_ID, items[0].id)

    chosen = FakeCallback(
        list_kb.callback_data(list_kb.STYLES, list_kb.DELETE, items[0].id)
    )
    await styles_handlers.press_delete_target(chosen, session)

    assert [s.name for s in await styles_crud.list_styles(session, USER_ID)] == [
        "techwear"
    ]
    assert chosen.answers[0][1] is True  # show_alert
    assert chosen.message.sent == []


async def test_cancel_clears_input_and_brings_the_list_back(session):
    await styles_crud.add_style(session, USER_ID, "gorpcore")
    state = FakeState()
    await styles_handlers.press_add(FakeCallback("x"), state)

    callback = FakeCallback(list_kb.callback_data(list_kb.STYLES, list_kb.CANCEL))
    await styles_handlers.press_cancel(callback, session, state)

    assert state.state is None
    assert "1. gorpcore" in callback.message.sent[0]
    assert "✏️ Переименовать" in _buttons(callback.message.markups[0])


async def test_empty_styles_offer_only_adding(session):
    message = FakeMessage()

    await styles_handlers.cmd_styles(message, session)

    assert _buttons(message.markups[0]) == ["➕ Добавить стиль"]


# --- гардероб -----------------------------------------------------------


async def test_wardrobe_add_button_flow(session):
    state = FakeState()
    callback = FakeCallback(list_kb.callback_data(list_kb.WARDROBE, list_kb.ADD))

    await wardrobe_handlers.press_add(callback, state)
    assert state.state == WardrobeInput.title
    assert "/add" not in callback.message.sent[0]

    message = FakeMessage("Серый свитшот Uniqlo, S")
    await wardrobe_handlers.receive_title(message, session, state)

    items = await wardrobe_crud.list_items(session, USER_ID)
    assert [item.title for item in items] == ["Серый свитшот Uniqlo, S"]
    assert "1. Серый свитшот Uniqlo, S" in message.sent[0]
    assert "🗑 Убрать" in _buttons(message.markups[0])


async def test_wardrobe_delete_button_removes_the_picked_item(session):
    for title in ("свитшот", "джинсы"):
        await wardrobe_crud.add_item(session, USER_ID, title=title)
    items = await wardrobe_crud.list_items(session, USER_ID)

    picker = FakeCallback(list_kb.callback_data(list_kb.WARDROBE, list_kb.DELETE))
    await wardrobe_handlers.press_delete(picker, session)
    assert "1. свитшот" in _buttons(picker.message.markups[0])[0]

    chosen = FakeCallback(
        list_kb.callback_data(list_kb.WARDROBE, list_kb.DELETE, items[0].id)
    )
    await wardrobe_handlers.press_delete_target(chosen, session)

    left = await wardrobe_crud.list_items(session, USER_ID)
    assert [item.title for item in left] == ["джинсы"]
    assert "🗑 Убрал: свитшот" in chosen.message.sent[0]


async def test_entering_the_section_drops_unfinished_input(session):
    """Иначе следующий текст уехал бы в брошенный вопрос вместо разбора вещи."""
    state = FakeState()
    await wardrobe_handlers.press_add(FakeCallback("x"), state)

    await wardrobe_handlers.cmd_wardrobe(FakeMessage(), session, state)

    assert state.state is None


# --- вишлист ------------------------------------------------------------


async def test_wishlist_add_button_flow(session):
    state = FakeState()
    await wishlist_handlers.press_add(
        FakeCallback(list_kb.callback_data(list_kb.WISHLIST, list_kb.ADD)), state
    )
    assert state.state == WishlistInput.title

    message = FakeMessage("Куртка Carhartt, 2500 грн")
    await wishlist_handlers.receive_title(message, session, state)

    items = await wishlist_crud.list_items(session, USER_ID)
    assert [item.title for item in items] == ["Куртка Carhartt, 2500 грн"]
    assert "🎉 Куплено" in _buttons(message.markups[0])


async def test_bought_button_moves_the_item_to_wardrobe(session):
    await wishlist_crud.add_item(session, USER_ID, title="Куртка Carhartt")
    item = (await wishlist_crud.list_items(session, USER_ID))[0]

    picker = FakeCallback(list_kb.callback_data(list_kb.WISHLIST, list_kb.BOUGHT))
    await wishlist_handlers.press_bought(picker, session)
    assert _callbacks(picker.message.markups[0])[0] == list_kb.callback_data(
        list_kb.WISHLIST, list_kb.BOUGHT, item.id
    )

    chosen = FakeCallback(
        list_kb.callback_data(list_kb.WISHLIST, list_kb.BOUGHT, item.id)
    )
    await wishlist_handlers.press_bought_target(chosen, session)

    assert [i.title for i in await wardrobe_crud.list_items(session, USER_ID)] == [
        "Куртка Carhartt"
    ]
    assert await wishlist_crud.list_items(session, USER_ID) == []
    # Правка задела оба списка — в ответе оба
    assert "🧥 <b>Гардероб</b>" in chosen.message.sent[0]
    assert "Вишлист пуст" in chosen.message.sent[0]


async def test_wishlist_delete_button_removes_the_picked_item(session):
    await wishlist_crud.add_item(session, USER_ID, title="Куртка")
    await wishlist_crud.add_item(session, USER_ID, title="Ботинки")
    items = await wishlist_crud.list_items(session, USER_ID)

    chosen = FakeCallback(
        list_kb.callback_data(list_kb.WISHLIST, list_kb.DELETE, items[1].id)
    )
    await wishlist_handlers.press_delete_target(chosen, session)

    assert [i.title for i in await wishlist_crud.list_items(session, USER_ID)] == [
        "Куртка"
    ]


# --- изоляция callback-данных -------------------------------------------


async def test_menu_buttons_do_not_collide_with_analysis_buttons(session):
    """«wardrobe:add:<разбор>» под разбором и «menu:wardrobe:add» из меню —
    разные потоки, и общий фильтр по «wardrobe:» ловил бы оба."""
    assert _matching_handlers(
        wardrobe_handlers.router, f"{ADD_TO_WARDROBE_PREFIX}:5"
    ) == ["add_from_analysis"]
    assert _matching_handlers(
        wardrobe_handlers.router, list_kb.callback_data(list_kb.WARDROBE, list_kb.ADD)
    ) == ["press_add"]
    assert _matching_handlers(
        wardrobe_handlers.router,
        list_kb.callback_data(list_kb.WARDROBE, list_kb.DELETE, 3),
    ) == ["press_delete_target"]
    assert _matching_handlers(
        wishlist_handlers.router, f"{ADD_TO_WISHLIST_PREFIX}:7"
    ) == ["add_from_analysis"]


async def test_every_menu_action_has_exactly_one_handler(session):
    cases = [
        (styles_handlers.router, list_kb.STYLES, list_kb.ADD, None),
        (styles_handlers.router, list_kb.STYLES, list_kb.RENAME, None),
        (styles_handlers.router, list_kb.STYLES, list_kb.RENAME, 1),
        (styles_handlers.router, list_kb.STYLES, list_kb.DELETE, None),
        (styles_handlers.router, list_kb.STYLES, list_kb.DELETE, 1),
        (styles_handlers.router, list_kb.STYLES, list_kb.CANCEL, None),
        (wardrobe_handlers.router, list_kb.WARDROBE, list_kb.ADD, None),
        (wardrobe_handlers.router, list_kb.WARDROBE, list_kb.DELETE, None),
        (wardrobe_handlers.router, list_kb.WARDROBE, list_kb.DELETE, 2),
        (wardrobe_handlers.router, list_kb.WARDROBE, list_kb.CANCEL, None),
        (wishlist_handlers.router, list_kb.WISHLIST, list_kb.ADD, None),
        (wishlist_handlers.router, list_kb.WISHLIST, list_kb.BOUGHT, None),
        (wishlist_handlers.router, list_kb.WISHLIST, list_kb.BOUGHT, 4),
        (wishlist_handlers.router, list_kb.WISHLIST, list_kb.DELETE, None),
        (wishlist_handlers.router, list_kb.WISHLIST, list_kb.DELETE, 4),
        (wishlist_handlers.router, list_kb.WISHLIST, list_kb.CANCEL, None),
    ]
    for router, section, action, item_id in cases:
        data = list_kb.callback_data(section, action, item_id)
        assert len(_matching_handlers(router, data)) == 1, data

