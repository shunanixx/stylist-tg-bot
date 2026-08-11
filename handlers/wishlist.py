import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import submissions as submissions_crud
from db.crud import wardrobe as wardrobe_crud
from db.crud import wishlist as wishlist_crud
from keyboards.analysis_kb import ADD_TO_WISHLIST_PREFIX
from services.listing import position_of, resolve_position

logger = logging.getLogger(__name__)
router = Router(name="wishlist")

VERDICT_ICONS = {"брать": "✅", "не брать": "❌"}

USAGE = (
    "<b>Вишлист</b>\n"
    "/wishlist — список\n"
    "/wish Куртка Carhartt, 2500 грн — отложить вручную\n"
    "/bought 3 — куплено, переносим в гардероб\n"
    "/unwish 3 — убрать из вишлиста"
)


@router.message(Command("wishlist"))
async def cmd_wishlist(message: Message, session: AsyncSession) -> None:
    items = await wishlist_crud.list_items(session, message.from_user.id)
    if not items:
        await message.answer(
            "Вишлист пуст. После разбора жми «⭐ В вишлист» — вещь попадёт сюда.\n\n"
            + USAGE
        )
        return

    lines = []
    # Номер — позиция в списке, а не item.id: иначе после /unwish и /bought
    # в нумерации остаются дыры, а новая вещь получает следующий id из БД.
    for position, item in enumerate(items, start=1):
        icon = VERDICT_ICONS.get(item.verdict or "", "•")
        details = ", ".join(str(v) for v in (item.category, item.note) if v)
        suffix = f" — {details}" if details else ""
        lines.append(f"{icon} {position}. {item.title}{suffix}")

    await message.answer(
        f"<b>Вишлист</b> — {len(items)} шт.\n" + "\n".join(lines) + "\n\n" + USAGE
    )


@router.message(Command("wish"))
async def cmd_wish(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    title = (command.args or "").strip()
    if not title:
        await message.answer("Что откладываем: <code>/wish Куртка Carhartt, 2500 грн</code>")
        return
    item = await wishlist_crud.add_item(session, message.from_user.id, title=title)

    items = await wishlist_crud.list_items(session, message.from_user.id)
    await message.answer(
        f"⭐ Отложил: {item.title} (№{position_of(items, item) or len(items)})"
    )


@router.message(Command("unwish"))
async def cmd_unwish(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    raw = (command.args or "").strip()
    items = await wishlist_crud.list_items(session, message.from_user.id)
    target = resolve_position(items, raw)
    if target is None:
        await message.answer(_no_such_number(items, "unwish"))
        return

    await wishlist_crud.deactivate_item(session, message.from_user.id, target.id)
    await message.answer(f"Убрал из вишлиста: {target.title}")


@router.message(Command("bought"))
async def cmd_bought(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    """Купленная вещь уходит из вишлиста в гардероб — иначе она осталась бы
    в обоих списках и модель считала бы её и «моей», и «присматриваемой»."""
    raw = (command.args or "").strip()
    items = await wishlist_crud.list_items(session, message.from_user.id)
    target = resolve_position(items, raw)
    if target is None:
        await message.answer(_no_such_number(items, "bought"))
        return

    added = await wardrobe_crud.add_item(
        session,
        message.from_user.id,
        title=target.title,
        category=target.category,
        source_submission_id=target.source_submission_id,
    )
    await wishlist_crud.deactivate_item(session, message.from_user.id, target.id)

    wardrobe_items = await wardrobe_crud.list_items(session, message.from_user.id)
    number = position_of(wardrobe_items, added) or len(wardrobe_items)
    await message.answer(f"🎉 {added.title} теперь в гардеробе (№{number}).")


def _no_such_number(items: list, command: str) -> str:
    if not items:
        return "Вишлист пуст."
    return (
        f"Нет вещи с таким номером. Сейчас в вишлисте {len(items)} шт., "
        f"номер бери из /wishlist: <code>/{command} 2</code>"
    )


@router.callback_query(F.data.startswith(f"{ADD_TO_WISHLIST_PREFIX}:"))
async def add_from_analysis(callback: CallbackQuery, session: AsyncSession) -> None:
    submission_id = int(callback.data.rsplit(":", 1)[1])
    submission = await submissions_crud.get_submission(
        session, callback.from_user.id, submission_id
    )
    if submission is None or not submission.item_title:
        await callback.answer("Не нашёл разбор для этой вещи.", show_alert=True)
        return

    existing = await wishlist_crud.find_by_submission(
        session, callback.from_user.id, submission_id
    )
    if existing is not None:
        await callback.answer("Эта вещь уже в вишлисте.", show_alert=True)
        return

    item = await wishlist_crud.add_item(
        session,
        callback.from_user.id,
        title=submission.item_title,
        category=submission.item_category,
        # вердикт фиксируем на момент добавления: пересматривать вишлист
        # осмысленно именно по нему
        verdict=submission.final_verdict,
        source_submission_id=submission_id,
    )
    await callback.answer(f"⭐ В вишлисте: {item.title}")
    if callback.message is not None:
        await callback.message.edit_reply_markup(reply_markup=None)
