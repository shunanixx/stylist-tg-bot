import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import submissions as submissions_crud
from db.crud import wardrobe as wardrobe_crud
from db.crud import wishlist as wishlist_crud
from handlers import list_ui
from handlers.wardrobe import render_wardrobe
from keyboards import list_kb
from keyboards.analysis_kb import ADD_TO_WISHLIST_PREFIX
from services.listing import by_id, position_of, resolve_position
from states.list_states import WishlistInput

logger = logging.getLogger(__name__)
router = Router(name="wishlist")

VERDICT_ICONS = {"брать": "✅", "не брать": "❌"}

USAGE = (
    "Кнопки под списком делают то же самое, команды — если быстрее набрать:\n"
    "/wish Куртка Carhartt, 2500 грн — отложить вручную\n"
    "/bought 3 — куплено, переносим в гардероб\n"
    "/unwish 3 — убрать из вишлиста"
)

EMPTY = "⭐ Вишлист пуст."

ASK_TITLE = (
    "⭐ Что отложить? Пришли обычным текстом — команда не нужна.\n"
    "Полезно добавить цену и место: <i>Куртка Carhartt, 2500 грн, OLX</i>"
)
ASK_PICK_DELETE = "🗑 Что убрать из вишлиста?"
ASK_PICK_BOUGHT = "🎉 Что куплено? Вещь переедет в гардероб."
GONE = "Этой вещи уже нет в вишлисте."


def render_wishlist(items: list) -> str:
    """Тот же список и на /wishlist, и после /wish, /unwish, /bought: номера
    после каждой правки сдвигаются, и показать их сразу дешевле, чем ловить
    удаление не той вещи по устаревшему номеру."""
    if not items:
        return EMPTY

    lines = []
    for position, item in enumerate(items, start=1):
        icon = VERDICT_ICONS.get(item.verdict or "", "•")
        details = ", ".join(str(v) for v in (item.category, item.note) if v)
        suffix = f" — {details}" if details else ""
        lines.append(f"{icon} {position}. {item.title}{suffix}")

    return f"⭐ <b>Вишлист</b> — {len(items)} шт.\n" + "\n".join(lines)


async def _answer_list(
    answerable, session: AsyncSession, user_id: int, prefix: str = ""
) -> None:
    """Список с кнопками. `user_id` отдельно: у сообщения из callback
    `from_user` — это бот."""
    items = await wishlist_crud.list_items(session, user_id)
    await answerable.answer(
        prefix + render_wishlist(items), reply_markup=list_kb.wishlist_kb(items)
    )


@router.message(Command("wishlist"))
async def cmd_wishlist(
    message: Message, session: AsyncSession, state: FSMContext | None = None
) -> None:
    # Вход в раздел закрывает незаконченный ввод
    if state is not None:
        await state.clear()
    items = await wishlist_crud.list_items(session, message.from_user.id)
    if not items:
        await message.answer(
            "⭐ Вишлист пуст. После разбора жми «⭐ В вишлист» — вещь попадёт сюда.\n\n"
            + USAGE,
            reply_markup=list_kb.wishlist_kb(items),
        )
        return
    await message.answer(
        render_wishlist(items) + "\n\n" + USAGE,
        reply_markup=list_kb.wishlist_kb(items),
    )


@router.message(Command("wish"))
async def cmd_wish(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    title = (command.args or "").strip()
    if not title:
        await message.answer(
            "Что откладываем: <code>/wish Куртка Carhartt, 2500 грн</code>\n"
            "Или жми «➕ Отложить» под списком — /wishlist."
        )
        return
    await _wish(message, session, message.from_user.id, title)


async def _wish(answerable, session: AsyncSession, user_id: int, title: str) -> None:
    item = await wishlist_crud.add_item(session, user_id, title=title)
    items = await wishlist_crud.list_items(session, user_id)
    number = position_of(items, item) or len(items)
    await _answer_list(
        answerable, session, user_id, prefix=f"⭐ Отложил: {item.title} (№{number})\n\n"
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
    await _unwish(message, session, message.from_user.id, target)


async def _unwish(answerable, session: AsyncSession, user_id: int, target) -> None:
    await wishlist_crud.deactivate_item(session, user_id, target.id)
    await _answer_list(
        answerable, session, user_id, prefix=f"🗑 Убрал из вишлиста: {target.title}\n\n"
    )


@router.message(Command("bought"))
async def cmd_bought(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    raw = (command.args or "").strip()
    items = await wishlist_crud.list_items(session, message.from_user.id)
    target = resolve_position(items, raw)
    if target is None:
        await message.answer(_no_such_number(items, "bought"))
        return
    await _buy(message, session, message.from_user.id, target)


async def _buy(answerable, session: AsyncSession, user_id: int, target) -> None:
    """Купленная вещь уходит из вишлиста в гардероб — иначе она осталась бы
    в обоих списках и модель считала бы её и «моей», и «присматриваемой»."""
    added = await wardrobe_crud.add_item(
        session,
        user_id,
        title=target.title,
        category=target.category,
        source_submission_id=target.source_submission_id,
    )
    await wishlist_crud.deactivate_item(session, user_id, target.id)

    wardrobe_items = await wardrobe_crud.list_items(session, user_id)
    number = position_of(wardrobe_items, added) or len(wardrobe_items)
    # Правка задела оба списка — показываем оба, номера в них разные. Кнопки
    # остаются от вишлиста: следующее действие почти наверняка снова в нём.
    await _answer_list(
        answerable,
        session,
        user_id,
        prefix=f"🎉 {added.title} теперь в гардеробе (№{number}).\n\n"
        + render_wardrobe(wardrobe_items)
        + "\n\n",
    )


def _no_such_number(items: list, command: str) -> str:
    if not items:
        return EMPTY
    return (
        f"Нет вещи с таким номером. Сейчас в вишлисте {len(items)} шт., "
        f"номер бери из /wishlist: <code>/{command} 2</code> — "
        "или жми кнопку под списком."
    )


# --- кнопки под списком -------------------------------------------------


@router.callback_query(F.data == list_kb.callback_data(list_kb.WISHLIST, list_kb.ADD))
async def press_add(callback: CallbackQuery, state: FSMContext) -> None:
    await list_ui.ask_text(
        callback, state, list_kb.WISHLIST, WishlistInput.title, ASK_TITLE
    )


@router.message(StateFilter(WishlistInput.title))
async def receive_title(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Ответ на «➕ Отложить»: обычный текст, без команды."""
    title = _plain_text(message)
    if title is None:
        await message.answer(
            "Название вещи — обычным текстом.",
            reply_markup=list_kb.cancel_kb(list_kb.WISHLIST),
        )
        return
    await state.clear()
    await _wish(message, session, message.from_user.id, title)


@router.callback_query(F.data == list_kb.callback_data(list_kb.WISHLIST, list_kb.DELETE))
async def press_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    items = await wishlist_crud.list_items(session, callback.from_user.id)
    await list_ui.ask_pick(
        callback,
        list_kb.WISHLIST,
        list_kb.DELETE,
        items,
        lambda item: item.title,
        ASK_PICK_DELETE,
        "Вишлист пуст.",
    )


@router.callback_query(
    F.data.startswith(list_kb.item_prefix(list_kb.WISHLIST, list_kb.DELETE))
)
async def press_delete_target(callback: CallbackQuery, session: AsyncSession) -> None:
    items = await wishlist_crud.list_items(session, callback.from_user.id)
    target = by_id(items, list_kb.item_id_from(callback.data))
    if target is None:
        await callback.answer(GONE, show_alert=True)
        return
    await list_ui.drop_keyboard(callback)
    await callback.answer(f"Убрал: {list_kb.shorten(target.title)}")
    if callback.message is not None:
        await _unwish(callback.message, session, callback.from_user.id, target)


@router.callback_query(F.data == list_kb.callback_data(list_kb.WISHLIST, list_kb.BOUGHT))
async def press_bought(callback: CallbackQuery, session: AsyncSession) -> None:
    items = await wishlist_crud.list_items(session, callback.from_user.id)
    await list_ui.ask_pick(
        callback,
        list_kb.WISHLIST,
        list_kb.BOUGHT,
        items,
        lambda item: item.title,
        ASK_PICK_BOUGHT,
        "Вишлист пуст.",
    )


@router.callback_query(
    F.data.startswith(list_kb.item_prefix(list_kb.WISHLIST, list_kb.BOUGHT))
)
async def press_bought_target(callback: CallbackQuery, session: AsyncSession) -> None:
    items = await wishlist_crud.list_items(session, callback.from_user.id)
    target = by_id(items, list_kb.item_id_from(callback.data))
    if target is None:
        await callback.answer(GONE, show_alert=True)
        return
    await list_ui.drop_keyboard(callback)
    await callback.answer(f"В гардероб: {list_kb.shorten(target.title)}")
    if callback.message is not None:
        await _buy(callback.message, session, callback.from_user.id, target)


@router.callback_query(F.data == list_kb.callback_data(list_kb.WISHLIST, list_kb.CANCEL))
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


def _plain_text(message: Message) -> str | None:
    raw = (message.text or "").strip()
    if not raw or raw.startswith("/"):
        return None
    return raw


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
        await _answer_list(
            callback.message,
            session,
            callback.from_user.id,
            prefix=f"⭐ Отложил: {item.title}\n\n",
        )
