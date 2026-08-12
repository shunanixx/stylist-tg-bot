import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import submissions as submissions_crud
from db.crud import wardrobe as wardrobe_crud
from handlers import list_ui
from keyboards import list_kb
from keyboards.analysis_kb import ADD_TO_WARDROBE_PREFIX
from services.listing import by_id, position_of, resolve_position
from states.list_states import WardrobeInput

logger = logging.getLogger(__name__)
router = Router(name="wardrobe")

USAGE = (
    "Кнопки под списком делают то же самое, команды — если быстрее набрать:\n"
    "/add Серый свитшот S — добавить вещь\n"
    "/remove 3 — убрать вещь по номеру из списка"
)

EMPTY = "🧥 Гардероб пуст."

ASK_TITLE = (
    "🧥 Пришли вещь обычным текстом — команда не нужна.\n"
    "Чем подробнее, тем полезнее в разборе: <i>Серый свитшот Uniqlo, S</i>"
)
ASK_PICK_DELETE = "🗑 Какую вещь убрать?"
GONE = "Этой вещи уже нет в гардеробе."


def render_wardrobe(items: list) -> str:
    """Список одним куском: он же ответ на /wardrobe, он же — подтверждение
    после /add и /remove, чтобы номера сразу были видны обновлёнными."""
    if not items:
        return EMPTY

    lines = []
    # Нумерация по позиции, а не по item.id: после удаления список остаётся
    # плотным (1, 2, 3), без дыр от сквозного автоинкремента БД.
    for position, item in enumerate(items, start=1):
        details = ", ".join(
            str(value) for value in (item.color, item.size, item.category) if value
        )
        suffix = f" ({details})" if details else ""
        lines.append(f"{position}. {item.title}{suffix}")

    return f"🧥 <b>Гардероб</b> — {len(items)} шт.\n" + "\n".join(lines)


async def _answer_list(
    answerable, session: AsyncSession, user_id: int, prefix: str = ""
) -> None:
    """Список с кнопками. `user_id` отдельно: у сообщения из callback
    `from_user` — это бот, и список получился бы чужой."""
    items = await wardrobe_crud.list_items(session, user_id)
    await answerable.answer(
        prefix + render_wardrobe(items), reply_markup=list_kb.wardrobe_kb(items)
    )


@router.message(Command("wardrobe"))
async def cmd_wardrobe(
    message: Message, session: AsyncSession, state: FSMContext | None = None
) -> None:
    # Вход в раздел закрывает незаконченный ввод: иначе следующий текст уехал
    # бы в него, а не в разбор вещи
    if state is not None:
        await state.clear()
    items = await wardrobe_crud.list_items(session, message.from_user.id)
    await message.answer(
        render_wardrobe(items) + "\n\n" + USAGE,
        reply_markup=list_kb.wardrobe_kb(items),
    )


@router.message(Command("add"))
async def cmd_add(
    message: Message, command: CommandObject, session: AsyncSession
) -> None:
    title = (command.args or "").strip()
    if not title:
        await message.answer(
            "Что добавляем: <code>/add Серый свитшот S</code>\n"
            "Или жми «➕ Добавить» под списком — /wardrobe."
        )
        return
    await _add(message, session, message.from_user.id, title)


async def _add(answerable, session: AsyncSession, user_id: int, title: str) -> None:
    item = await wardrobe_crud.add_item(session, user_id, title=title)
    items = await wardrobe_crud.list_items(session, user_id)
    number = position_of(items, item) or len(items)
    await _answer_list(
        answerable, session, user_id, prefix=f"✅ Добавил: {item.title} (№{number})\n\n"
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
            "🧥 Гардероб пуст — удалять нечего."
            if not items
            else f"Нет вещи с таким номером. Сейчас в гардеробе {len(items)} шт., "
            "номер бери из /wardrobe: <code>/remove 3</code> — "
            "или жми «🗑 Убрать» под списком."
        )
        return
    await _remove(message, session, message.from_user.id, target)


async def _remove(answerable, session: AsyncSession, user_id: int, target) -> None:
    await wardrobe_crud.deactivate_item(session, user_id, target.id)
    # Номера сдвинулись — показываем список сразу, иначе следующий /remove
    # уйдёт по старому номеру и удалит не ту вещь.
    await _answer_list(
        answerable, session, user_id, prefix=f"🗑 Убрал: {target.title}\n\n"
    )


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
        # Всплывашка исчезает через секунду — обновлённый список остаётся в чате
        await _answer_list(
            callback.message,
            session,
            callback.from_user.id,
            prefix=f"✅ В гардеробе: {item.title}\n\n",
        )


# --- кнопки под списком -------------------------------------------------


@router.callback_query(F.data == list_kb.callback_data(list_kb.WARDROBE, list_kb.ADD))
async def press_add(callback: CallbackQuery, state: FSMContext) -> None:
    await list_ui.ask_text(
        callback, state, list_kb.WARDROBE, WardrobeInput.title, ASK_TITLE
    )


@router.message(StateFilter(WardrobeInput.title))
async def receive_title(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    """Ответ на «➕ Добавить»: описание вещи обычным текстом, без команды."""
    title = _plain_text(message)
    if title is None:
        await message.answer(
            "Название вещи — обычным текстом.",
            reply_markup=list_kb.cancel_kb(list_kb.WARDROBE),
        )
        return
    await state.clear()
    await _add(message, session, message.from_user.id, title)


@router.callback_query(F.data == list_kb.callback_data(list_kb.WARDROBE, list_kb.DELETE))
async def press_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    items = await wardrobe_crud.list_items(session, callback.from_user.id)
    await list_ui.ask_pick(
        callback,
        list_kb.WARDROBE,
        list_kb.DELETE,
        items,
        lambda item: item.title,
        ASK_PICK_DELETE,
        "Гардероб пуст.",
    )


@router.callback_query(
    F.data.startswith(list_kb.item_prefix(list_kb.WARDROBE, list_kb.DELETE))
)
async def press_delete_target(callback: CallbackQuery, session: AsyncSession) -> None:
    items = await wardrobe_crud.list_items(session, callback.from_user.id)
    target = by_id(items, list_kb.item_id_from(callback.data))
    if target is None:
        await callback.answer(GONE, show_alert=True)
        return
    await list_ui.drop_keyboard(callback)
    await callback.answer(f"Убрал: {list_kb.shorten(target.title)}")
    if callback.message is not None:
        await _remove(callback.message, session, callback.from_user.id, target)


@router.callback_query(F.data == list_kb.callback_data(list_kb.WARDROBE, list_kb.CANCEL))
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
    """Только текст: фото в этом стейте — почти наверняка вещь на разбор,
    а не название, и неизвестная команда тоже не название."""
    raw = (message.text or "").strip()
    if not raw or raw.startswith("/"):
        return None
    return raw
