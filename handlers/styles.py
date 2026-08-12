import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import styles as styles_crud
from db.crud.styles import MAX_NAME_LENGTH
from handlers import list_ui
from keyboards import list_kb
from services.listing import by_id, resolve_position
from states.list_states import StyleInput

logger = logging.getLogger(__name__)
router = Router(name="styles")

# Разделитель для переименования: стили — фразы с пробелами, поэтому
# «/style_edit 2 новое имя» разобрать однозначно нельзя.
RENAME_SEPARATOR = "->"
# Один вызов /style_add может принести список через запятую
LIST_SEPARATORS = (",", ";", "\n")

USAGE = (
    "Кнопки под списком делают то же самое, команды — на случай, когда быстрее набрать:\n"
    "/style_add минимализм, casual — добавить (можно списком)\n"
    "/style_edit 2 -> workwear — переименовать\n"
    "/style_del 2 — убрать"
)

EMPTY_HINT = (
    "🎨 Стили не заданы. Перечисли, в чём ты одеваешься — по ним пойдёт разбор.\n"
    "Жми «➕ Добавить стиль» или пришли командой: <code>/style_add минимализм, casual</code>\n\n"
    "Подойдут и названия направлений, и описание своими словами — "
    "«тёмный верх, широкий низ» тоже сгодится.\n\n"
    "Без стилей бот назовёт стиль вещи как есть, но не сможет сказать, "
    "твоё это или нет."
)

ASK_NAME = (
    "🎨 Пришли название стиля обычным текстом — команда не нужна.\n"
    "Можно сразу списком через запятую: <i>минимализм, casual, old money</i>"
)
ASK_PICK_RENAME = "✏️ Какой стиль переименовать?"
ASK_PICK_DELETE = "🗑 Какой стиль убрать?"
GONE = "Этого стиля уже нет в списке."


def render_styles(items: list) -> str:
    """Список после каждой правки: номера в нём — единственный способ
    сослаться на стиль в /style_edit и /style_del."""
    if not items:
        return "🎨 Стилей не осталось — разбор пойдёт без привязки к твоему вкусу."
    lines = [f"{position}. {item.name}" for position, item in enumerate(items, start=1)]
    return f"🎨 <b>Мои стили</b> — {len(items)} шт.\n" + "\n".join(lines)


async def _answer_list(
    answerable, session: AsyncSession, user_id: int, prefix: str = "", suffix: str = ""
) -> None:
    """Список плюс кнопки: следующее действие делается тут же, без команд.

    `user_id` передаётся отдельно: у сообщения из callback `from_user` — это
    бот, и брать id оттуда значило бы работать с чужим списком.
    """
    items = await styles_crud.list_styles(session, user_id)
    await answerable.answer(
        prefix + render_styles(items) + suffix,
        reply_markup=list_kb.styles_kb(items),
    )


# --- команды ------------------------------------------------------------


@router.message(Command("styles"))
async def cmd_styles(
    message: Message, session: AsyncSession, state: FSMContext | None = None
) -> None:
    # Вход в раздел закрывает незаконченный ввод, иначе следующий текст
    # уехал бы в него вместо описания вещи
    if state is not None:
        await state.clear()
    items = await styles_crud.list_styles(session, message.from_user.id)
    if not items:
        await message.answer(EMPTY_HINT, reply_markup=list_kb.styles_kb(items))
        return
    await message.answer(
        render_styles(items) + "\n\n" + USAGE, reply_markup=list_kb.styles_kb(items)
    )


@router.message(Command("style_add"))
async def cmd_style_add(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Что добавляем: <code>/style_add минимализм</code>\n"
            "Можно списком: <code>/style_add минимализм, casual, sport</code>\n"
            "Или жми «➕ Добавить» под списком — /styles.",
        )
        return
    await _add_names(message, session, message.from_user.id, raw)


async def _add_names(
    answerable, session: AsyncSession, user_id: int, raw: str
) -> None:
    """Общий разбор списка имён: и для /style_add, и для ввода после кнопки."""
    added: list[str] = []
    skipped: list[str] = []
    too_long: list[str] = []

    for name in _split_names(raw):
        if len(name) > MAX_NAME_LENGTH:
            too_long.append(name)
            continue
        # Дубликат — не ошибка, но и второй раз в промпт его слать незачем
        if await styles_crud.find_by_name(session, user_id, name):
            skipped.append(name)
            continue
        await styles_crud.add_style(session, user_id, name)
        added.append(name)

    parts = []
    if added:
        parts.append(f"✅ Добавил: {', '.join(added)}")
    if skipped:
        parts.append(f"Уже есть: {', '.join(skipped)}")
    if too_long:
        parts.append(
            f"Слишком длинно (больше {MAX_NAME_LENGTH} символов): {len(too_long)} шт."
        )
    if not parts:
        parts.append("Не понял, что добавлять.")

    await _answer_list(answerable, session, user_id, prefix="\n".join(parts) + "\n\n")


@router.message(Command("style_edit"))
async def cmd_style_edit(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    raw = (command.args or "").strip()
    if RENAME_SEPARATOR not in raw:
        await message.answer(
            "Формат: <code>/style_edit 2 -> workwear</code>\n"
            "Номер бери из /styles — или жми «✏️ Переименовать» под списком."
        )
        return

    position_part, _, new_name = raw.partition(RENAME_SEPARATOR)
    items = await styles_crud.list_styles(session, message.from_user.id)
    target = resolve_position(items, position_part.strip())
    if target is None:
        await message.answer(_no_such_number(items))
        return
    await _rename(message, session, message.from_user.id, target, new_name.strip())


async def _rename(
    answerable, session: AsyncSession, user_id: int, target, new_name: str
) -> None:
    if not new_name:
        await answerable.answer("Нужно новое название стиля — пришли текстом.")
        return
    if len(new_name) > MAX_NAME_LENGTH:
        await answerable.answer(
            f"Слишком длинно — не больше {MAX_NAME_LENGTH} символов."
        )
        return

    old_name = target.name
    await styles_crud.rename_style(session, user_id, target.id, new_name)
    await _answer_list(
        answerable, session, user_id, prefix=f"✏️ {old_name} → {new_name}\n\n"
    )


@router.message(Command("style_del"))
async def cmd_style_del(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    items = await styles_crud.list_styles(session, message.from_user.id)
    target = resolve_position(items, (command.args or "").strip())
    if target is None:
        await message.answer(_no_such_number(items))
        return
    await _delete(message, session, message.from_user.id, target)


async def _delete(answerable, session: AsyncSession, user_id: int, target) -> None:
    await styles_crud.delete_style(session, user_id, target.id)
    left = await styles_crud.list_styles(session, user_id)
    tail = "\nДобавь новые: /style_add или кнопкой «➕»" if not left else ""
    await _answer_list(
        answerable, session, user_id, prefix=f"🗑 Убрал: {target.name}\n\n", suffix=tail
    )


# --- кнопки под списком -------------------------------------------------


@router.callback_query(F.data == list_kb.callback_data(list_kb.STYLES, list_kb.ADD))
async def press_add(callback: CallbackQuery, state: FSMContext) -> None:
    await list_ui.ask_text(callback, state, list_kb.STYLES, StyleInput.name, ASK_NAME)


@router.message(StateFilter(StyleInput.name))
async def receive_name(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Ответ на кнопку «➕ Добавить»: обычный текст, без команды."""
    raw = _plain_text(message)
    if raw is None:
        await message.answer(
            "Название стиля — обычным текстом.",
            reply_markup=list_kb.cancel_kb(list_kb.STYLES),
        )
        return
    await state.clear()
    await _add_names(message, session, message.from_user.id, raw)


@router.callback_query(F.data == list_kb.callback_data(list_kb.STYLES, list_kb.RENAME))
async def press_rename(callback: CallbackQuery, session: AsyncSession) -> None:
    items = await styles_crud.list_styles(session, callback.from_user.id)
    await list_ui.ask_pick(
        callback,
        list_kb.STYLES,
        list_kb.RENAME,
        items,
        lambda style: style.name,
        ASK_PICK_RENAME,
        "Стилей пока нет.",
    )


@router.callback_query(
    F.data.startswith(list_kb.item_prefix(list_kb.STYLES, list_kb.RENAME))
)
async def press_rename_target(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    items = await styles_crud.list_styles(session, callback.from_user.id)
    target = by_id(items, list_kb.item_id_from(callback.data))
    if target is None:
        await callback.answer(GONE, show_alert=True)
        return
    await list_ui.ask_text(
        callback,
        state,
        list_kb.STYLES,
        StyleInput.new_name,
        f"✏️ Как теперь называть «{target.name}»? Пришли новое название текстом.",
        style_id=target.id,
    )


@router.message(StateFilter(StyleInput.new_name))
async def receive_new_name(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    new_name = _plain_text(message)
    if new_name is None:
        await message.answer(
            "Новое название — обычным текстом.",
            reply_markup=list_kb.cancel_kb(list_kb.STYLES),
        )
        return

    data = await state.get_data()
    items = await styles_crud.list_styles(session, message.from_user.id)
    target = by_id(items, data.get("style_id"))
    await state.clear()
    if target is None:
        await message.answer(GONE)
        return
    await _rename(message, session, message.from_user.id, target, new_name)


@router.callback_query(F.data == list_kb.callback_data(list_kb.STYLES, list_kb.DELETE))
async def press_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    items = await styles_crud.list_styles(session, callback.from_user.id)
    await list_ui.ask_pick(
        callback,
        list_kb.STYLES,
        list_kb.DELETE,
        items,
        lambda style: style.name,
        ASK_PICK_DELETE,
        "Стилей пока нет.",
    )


@router.callback_query(
    F.data.startswith(list_kb.item_prefix(list_kb.STYLES, list_kb.DELETE))
)
async def press_delete_target(
    callback: CallbackQuery, session: AsyncSession
) -> None:
    items = await styles_crud.list_styles(session, callback.from_user.id)
    target = by_id(items, list_kb.item_id_from(callback.data))
    if target is None:
        await callback.answer(GONE, show_alert=True)
        return
    await list_ui.drop_keyboard(callback)
    await callback.answer(f"Убрал: {list_kb.shorten(target.name)}")
    if callback.message is not None:
        await _delete(callback.message, session, callback.from_user.id, target)


@router.callback_query(F.data == list_kb.callback_data(list_kb.STYLES, list_kb.CANCEL))
async def press_cancel(
    callback: CallbackQuery, session: AsyncSession, state: FSMContext
) -> None:
    await state.clear()
    await list_ui.drop_keyboard(callback)
    await callback.answer("Отменил")
    if callback.message is not None:
        await _answer_list(
            callback.message, session, callback.from_user.id, prefix="↩️ Отменил.\n\n"
        )


# --- разбор ввода -------------------------------------------------------


def _plain_text(message: Message) -> str | None:
    """Ввод после кнопки — только текст. Стикер или фото приходят в тот же
    стейт, и молчать на них хуже, чем попросить текст ещё раз."""
    raw = (message.text or "").strip()
    # Неизвестную команду сюда пропускает роутер: сохранять «/foo» как стиль
    # пользователь точно не просил
    if not raw or raw.startswith("/"):
        return None
    return raw


def _split_names(raw: str) -> list[str]:
    parts = [raw]
    for separator in LIST_SEPARATORS:
        parts = [chunk for piece in parts for chunk in piece.split(separator)]
    return [name for name in (piece.strip() for piece in parts) if name]


def _no_such_number(items: list) -> str:
    if not items:
        return "Своих стилей пока нет — /style_add или кнопка «➕» в /styles."
    return (
        f"Нет стиля с таким номером. Сейчас их {len(items)}, "
        "номер бери из /styles."
    )
