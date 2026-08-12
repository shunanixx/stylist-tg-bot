"""Свои стили: список без ограничений, редактирование, попадание в промпт."""

import pytest
import pytest_asyncio

from db.crud import styles as styles_crud
from db.crud.styles import MAX_NAME_LENGTH
from db.database import Database
from handlers.styles import cmd_style_add, cmd_style_del, cmd_style_edit, cmd_styles
from services.prompt_builder import PromptBuilder

USER_ID = 4242
OTHER_USER_ID = 4243


class FakeMessage:
    def __init__(self, user_id: int = USER_ID):
        self.from_user = type("U", (), {"id": user_id})()
        self.sent: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.sent.append(text)


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


async def _names(session, user_id=USER_ID):
    return [s.name for s in await styles_crud.list_styles(session, user_id)]


# --- добавление ---------------------------------------------------------


async def test_add_single_style(session):
    message = FakeMessage()

    await cmd_style_add(message, FakeCommand("gorpcore"), session)

    assert await _names(session) == ["gorpcore"]


async def test_add_accepts_comma_separated_list(session):
    """Вводить стили по одному утомительно — принимаем списком."""
    message = FakeMessage()

    await cmd_style_add(message, FakeCommand("gorpcore, techwear, workwear"), session)

    assert await _names(session) == ["gorpcore", "techwear", "workwear"]


async def test_add_keeps_multiword_names(session):
    await cmd_style_add(FakeMessage(), FakeCommand("old money, skate (sk8)"), session)

    assert await _names(session) == ["old money", "skate (sk8)"]


async def test_duplicates_are_skipped_case_insensitively(session):
    """Дубль в промпте — лишние токены на каждом запросе."""
    await cmd_style_add(FakeMessage(), FakeCommand("Y2K"), session)

    message = FakeMessage()
    await cmd_style_add(message, FakeCommand("y2k, gorpcore"), session)

    assert await _names(session) == ["Y2K", "gorpcore"]
    assert "Уже есть" in message.sent[0]


async def test_unlimited_number_of_styles(session):
    """Верхней границы на количество стилей нет."""
    await cmd_style_add(
        FakeMessage(), FakeCommand(", ".join(f"стиль-{i}" for i in range(50))), session
    )

    assert len(await _names(session)) == 50


async def test_overly_long_name_rejected(session):
    message = FakeMessage()

    await cmd_style_add(message, FakeCommand("x" * (MAX_NAME_LENGTH + 1)), session)

    assert await _names(session) == []
    assert "длинно" in message.sent[0]


async def test_add_without_args_explains_format(session):
    message = FakeMessage()

    await cmd_style_add(message, FakeCommand(None), session)

    assert "/style_add" in message.sent[0]
    assert await _names(session) == []


# --- редактирование -----------------------------------------------------


async def test_edit_renames_by_position(session):
    await cmd_style_add(FakeMessage(), FakeCommand("gorpcore, techwear"), session)

    message = FakeMessage()
    await cmd_style_edit(message, FakeCommand("2 -> workwear"), session)

    assert await _names(session) == ["gorpcore", "workwear"]
    assert "techwear" in message.sent[0]


async def test_edit_keeps_multiword_new_name(session):
    """Разделитель «->», а не пробел: иначе «old money» не разобрать."""
    await cmd_style_add(FakeMessage(), FakeCommand("gorpcore"), session)

    await cmd_style_edit(FakeMessage(), FakeCommand("1 -> old money"), session)

    assert await _names(session) == ["old money"]


async def test_edit_without_separator_explains_format(session):
    await cmd_style_add(FakeMessage(), FakeCommand("gorpcore"), session)

    message = FakeMessage()
    await cmd_style_edit(message, FakeCommand("1 workwear"), session)

    assert await _names(session) == ["gorpcore"]
    assert "->" in message.sent[0]


async def test_edit_with_empty_new_name_changes_nothing(session):
    await cmd_style_add(FakeMessage(), FakeCommand("gorpcore"), session)

    message = FakeMessage()
    await cmd_style_edit(message, FakeCommand("1 -> "), session)

    assert await _names(session) == ["gorpcore"]
    assert message.sent


@pytest.mark.parametrize("args", ["0 -> x", "99 -> x", "нет -> x"])
async def test_edit_with_bad_number_changes_nothing(session, args):
    await cmd_style_add(FakeMessage(), FakeCommand("gorpcore"), session)

    message = FakeMessage()
    await cmd_style_edit(message, FakeCommand(args), session)

    assert await _names(session) == ["gorpcore"]
    assert "номер" in message.sent[0].lower()


# --- удаление и сброс ---------------------------------------------------


async def test_delete_by_position_keeps_numbering_dense(session):
    await cmd_style_add(FakeMessage(), FakeCommand("первый, второй, третий"), session)

    await cmd_style_del(FakeMessage(), FakeCommand("2"), session)

    listing = FakeMessage()
    await cmd_styles(listing, session)
    assert "1. первый" in listing.sent[0]
    assert "2. третий" in listing.sent[0]


async def test_deleting_last_style_tells_what_changes(session):
    await cmd_style_add(FakeMessage(), FakeCommand("gorpcore"), session)

    message = FakeMessage()
    await cmd_style_del(message, FakeCommand("1"), session)

    assert await _names(session) == []
    assert "/style_add" in message.sent[0]


async def test_styles_are_isolated_between_users(session):
    await styles_crud.add_style(session, OTHER_USER_ID, "чужой стиль")

    message = FakeMessage()
    await cmd_style_del(message, FakeCommand("1"), session)

    assert await _names(session, OTHER_USER_ID) == ["чужой стиль"]
    assert await _names(session) == []


async def test_empty_list_asks_to_add_own_styles(session):
    """Стартового набора нет: чужие стили = разбор в чужих координатах."""
    message = FakeMessage()

    await cmd_styles(message, session)

    assert "/style_add" in message.sent[0]
    assert "не заданы" in message.sent[0]


# --- промпт -------------------------------------------------------------


def test_styles_reach_prompt():
    from types import SimpleNamespace

    styles = [SimpleNamespace(name="gorpcore"), SimpleNamespace(name="old money")]
    prompt = PromptBuilder().build(None, [], [], [], styles)

    assert "[МОИ СТИЛИ]" in prompt
    assert "gorpcore, old money" in prompt


def test_prompt_states_styles_are_unset_instead_of_guessing():
    """Подставить чужой набор хуже, чем честно сказать, что стилей нет."""
    prompt = PromptBuilder().build(None, [], [], [], [])

    assert "[МОИ СТИЛИ]" in prompt
    assert "не заданы" in prompt
    assert "streetwear" not in prompt


def test_prompt_accepts_plain_strings():
    prompt = PromptBuilder().build(None, [], [], [], ["techwear"])

    assert "techwear" in prompt


# --- обновлённый список сразу после правки ------------------------------


async def test_add_answers_with_refreshed_list(session):
    message = FakeMessage()

    await cmd_style_add(message, FakeCommand("минимализм, casual"), session)

    assert "1. минимализм" in message.sent[0]
    assert "2. casual" in message.sent[0]


async def test_delete_answers_with_refreshed_list(session):
    await cmd_style_add(FakeMessage(), FakeCommand("первый, второй, третий"), session)

    message = FakeMessage()
    await cmd_style_del(message, FakeCommand("2"), session)

    assert "1. первый" in message.sent[0]
    assert "2. третий" in message.sent[0]


async def test_rename_answers_with_refreshed_list(session):
    await cmd_style_add(FakeMessage(), FakeCommand("gorpcore"), session)

    message = FakeMessage()
    await cmd_style_edit(message, FakeCommand("1 -> techwear"), session)

    assert "1. techwear" in message.sent[0]
