import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import styles as styles_crud
from db.crud.styles import MAX_NAME_LENGTH
from services.listing import resolve_position

logger = logging.getLogger(__name__)
router = Router(name="styles")

# Разделитель для переименования: стили — фразы с пробелами, поэтому
# «/style_edit 2 новое имя» разобрать однозначно нельзя.
RENAME_SEPARATOR = "->"
# Один вызов /style_add может принести список через запятую
LIST_SEPARATORS = (",", ";", "\n")

USAGE = (
    "<b>Мои стили</b>\n"
    "/styles — список\n"
    "/style_add минимализм, casual — добавить (можно списком)\n"
    "/style_edit 2 -> workwear — переименовать\n"
    "/style_del 2 — убрать"
)

EMPTY_HINT = (
    "Стили не заданы. Перечисли, в чём ты одеваешься — по ним пойдёт разбор:\n"
    "<code>/style_add минимализм, casual</code>\n\n"
    "Подойдут и названия направлений, и описание своими словами — "
    "«тёмный верх, широкий низ» тоже сгодится.\n\n"
    "Без стилей бот назовёт стиль вещи как есть, но не сможет сказать, "
    "твоё это или нет.\n\n" + USAGE
)


@router.message(Command("styles"))
async def cmd_styles(message: Message, session: AsyncSession) -> None:
    items = await styles_crud.list_styles(session, message.from_user.id)
    if not items:
        await message.answer(EMPTY_HINT)
        return

    lines = [f"{position}. {item.name}" for position, item in enumerate(items, start=1)]
    await message.answer(
        f"<b>Мои стили</b> — {len(items)} шт.\n" + "\n".join(lines) + "\n\n" + USAGE
    )


@router.message(Command("style_add"))
async def cmd_style_add(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Что добавляем: <code>/style_add минимализм</code>\n"
            "Можно списком: <code>/style_add минимализм, casual, sport</code>"
        )
        return

    user_id = message.from_user.id
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
        parts.append(f"Слишком длинно (больше {MAX_NAME_LENGTH} символов): {len(too_long)} шт.")
    if not parts:
        parts.append("Не понял, что добавлять.")

    total = await styles_crud.count_styles(session, user_id)
    parts.append(f"Всего стилей: {total} — /styles")
    await message.answer("\n".join(parts))


@router.message(Command("style_edit"))
async def cmd_style_edit(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    raw = (command.args or "").strip()
    if RENAME_SEPARATOR not in raw:
        await message.answer(
            "Формат: <code>/style_edit 2 -> workwear</code>\n"
            "Номер бери из /styles."
        )
        return

    position_part, _, new_name = raw.partition(RENAME_SEPARATOR)
    new_name = new_name.strip()
    items = await styles_crud.list_styles(session, message.from_user.id)
    target = resolve_position(items, position_part.strip())

    if target is None:
        await message.answer(_no_such_number(items))
        return
    if not new_name:
        await message.answer("После «->» нужно новое название стиля.")
        return
    if len(new_name) > MAX_NAME_LENGTH:
        await message.answer(f"Слишком длинно — не больше {MAX_NAME_LENGTH} символов.")
        return

    old_name = target.name
    await styles_crud.rename_style(session, message.from_user.id, target.id, new_name)
    await message.answer(f"✏️ {old_name} → {new_name}")


@router.message(Command("style_del"))
async def cmd_style_del(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    items = await styles_crud.list_styles(session, message.from_user.id)
    target = resolve_position(items, (command.args or "").strip())
    if target is None:
        await message.answer(_no_such_number(items))
        return

    await styles_crud.delete_style(session, message.from_user.id, target.id)
    left = await styles_crud.count_styles(session, message.from_user.id)
    tail = (
        "\nСтилей не осталось — разбор будет без привязки к твоему вкусу. "
        "Добавь новые: /style_add"
        if left == 0
        else ""
    )
    await message.answer(f"Убрал: {target.name}{tail}")


def _split_names(raw: str) -> list[str]:
    parts = [raw]
    for separator in LIST_SEPARATORS:
        parts = [chunk for piece in parts for chunk in piece.split(separator)]
    return [name for name in (piece.strip() for piece in parts) if name]


def _no_such_number(items: list) -> str:
    if not items:
        return "Своих стилей пока нет — /style_add, чтобы задать."
    return (
        f"Нет стиля с таким номером. Сейчас их {len(items)}, "
        "номер бери из /styles."
    )
