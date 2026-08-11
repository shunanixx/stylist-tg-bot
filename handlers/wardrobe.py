import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import submissions as submissions_crud
from db.crud import wardrobe as wardrobe_crud
from keyboards.analysis_kb import ADD_TO_WARDROBE_PREFIX
from services.listing import position_of, resolve_position

logger = logging.getLogger(__name__)
router = Router(name="wardrobe")

USAGE = (
    "<b>Гардероб</b>\n"
    "/wardrobe — список\n"
    "/add Серый свитшот S — добавить вещь\n"
    "/remove 3 — убрать вещь по номеру из списка"
)


@router.message(Command("wardrobe"))
async def cmd_wardrobe(message: Message, session: AsyncSession) -> None:
    items = await wardrobe_crud.list_items(session, message.from_user.id)
    if not items:
        await message.answer("Гардероб пуст.\n\n" + USAGE)
        return

    lines = []
    # Нумерация по позиции, а не по item.id: после удаления список остаётся
    # плотным (1, 2, 3), без дыр от сквозного автоинкремента БД.
    for position, item in enumerate(items, start=1):
        details = ", ".join(
            str(value) for value in (item.color, item.size, item.category) if value
        )
        suffix = f" ({details})" if details else ""
        lines.append(f"{position}. {item.title}{suffix}")

    await message.answer(
        f"<b>Гардероб</b> — {len(items)} шт.\n" + "\n".join(lines) + "\n\n" + USAGE
    )


@router.message(Command("add"))
async def cmd_add(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    title = (command.args or "").strip()
    if not title:
        await message.answer("Укажи вещь: <code>/add Серый свитшот S</code>")
        return
    item = await wardrobe_crud.add_item(session, message.from_user.id, title=title)

    items = await wardrobe_crud.list_items(session, message.from_user.id)
    await message.answer(
        f"Добавил: {item.title} (№{position_of(items, item) or len(items)})"
    )


@router.message(Command("remove"))
async def cmd_remove(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    raw = (command.args or "").strip()
    items = await wardrobe_crud.list_items(session, message.from_user.id)
    target = resolve_position(items, raw)
    if target is None:
        await message.answer(
            "Гардероб пуст — удалять нечего."
            if not items
            else f"Нет вещи с таким номером. Сейчас в гардеробе {len(items)} шт., "
            "номер бери из /wardrobe: <code>/remove 3</code>"
        )
        return

    await wardrobe_crud.deactivate_item(session, message.from_user.id, target.id)
    await message.answer(f"Убрал: {target.title}")


@router.callback_query(F.data.startswith(f"{ADD_TO_WARDROBE_PREFIX}:"))
async def add_from_analysis(callback: CallbackQuery, session: AsyncSession) -> None:
    submission_id = int(callback.data.rsplit(":", 1)[1])
    submission = await submissions_crud.get_submission(
        session, callback.from_user.id, submission_id
    )
    if submission is None or not submission.item_title:
        await callback.answer("Не нашёл разбор для этой вещи.", show_alert=True)
        return

    existing = await wardrobe_crud.list_items(session, callback.from_user.id)
    if any(item.source_submission_id == submission_id for item in existing):
        await callback.answer("Эта вещь уже в гардеробе.", show_alert=True)
        return

    item = await wardrobe_crud.add_item(
        session,
        callback.from_user.id,
        title=submission.item_title,
        category=submission.item_category,
        source_submission_id=submission_id,
    )
    await callback.answer(f"В гардеробе: {item.title}")
    if callback.message is not None:
        await callback.message.edit_reply_markup(reply_markup=None)
