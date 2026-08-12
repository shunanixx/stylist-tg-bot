from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from db.crud import users as users_crud
from db.crud import wardrobe as wardrobe_crud

# Клавиатура импортируется напрямую, а не через handlers.menu: menu.py
# импортирует start — обратная зависимость замкнула бы цикл.
from keyboards.menu_kb import main_menu_kb
from services.api_keys import NO_KEY_MESSAGE, resolve_api_key
from services.crypto import KeyVault

router = Router(name="start")

WELCOME = """👋 Я стилист. Работаю в системе координат твоих стилей — тех, что ты задал через /styles.

Пришли <b>описание вещи</b> или <b>фото</b> — разберу по 16 пунктам и скажу, брать или нет.

Разделы — на кнопках под полем ввода, внутри разделов кнопки добавляют и убирают вещи. Команды работают так же:
/apikey — свой ключ Gemini (нужен для работы)
/styles — стили, в которых я одеваюсь
/profile — параметры фигуры
/wardrobe — гардероб
/wishlist — отложено к покупке
/history — журнал вещей
/help — подробнее"""

HELP = """📖 <b>Как пользоваться</b>

Напиши описание вещи или пришли фото: бренд, размер, состояние, цену, ссылку — чем больше данных, тем точнее разбор. Например:
<i>Куртка Carhartt Detroit, размер M, б/у, 2500 грн, состояние 4/5</i>

Отвечу по 16 пунктам методологии: стиль, подлинность, ценовой сегмент, состояние, размер под твои параметры, сезон, слойность, уход, долговечность, тренд, цена/качество, сочетание со штанами, обувь, аксессуары, сочетание с гардеробом и вердикт.

🎨 <b>Стили</b>
Разбор идёт в системе координат твоих стилей: пункт 1 — к какому из них вещь ближе, пункт 10 — актуальность внутри них, пункт 15 — сочетаемость с гардеробом в их рамках. Перечисли свои — хоть общеизвестные, хоть описанные своими словами. Количество не ограничено, менять можно в любой момент — /styles.

🧩 <b>Списки на кнопках</b>
Стили, гардероб и вишлист правятся кнопками под списком: «➕ Добавить», «✏️ Переименовать», «🗑 Убрать», «🎉 Куплено». После нажатия просто пришли название обычным текстом — команду набирать не нужно, выйти — «↩️ Отмена». Удаление тоже кнопкой: жмёшь саму вещь, номер помнить не надо. Команды с аргументами остались рабочими: /style_add, /add, /wish, /bought.

📐 <b>Замеры</b>
Обхваты в /profile — <b>полные</b>: лентой вокруг тела, а не по плоскости вещи. Полуобхват бот считает сам — именно в нём продавцы указывают замеры вещи. Заполнить по шагам — /setup; вопросы и ответы бот убирает из чата сам, в конце присылает список параметров.

🔑 <b>Ключ Gemini</b>
Бот работает на твоём ключе, поэтому твои разборы не упираются в чужой лимит. Ключ бесплатный: <a href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a> → «Create API key» → пришли <code>/apikey ВСТАВЬ_КЛЮЧ</code>. Сообщение с ключом бот удаляет сразу, ключ хранится зашифрованным. Убрать — /apikey_off.

🧭 <b>Команды</b>
/menu — вернуть кнопки под полем ввода
/apikey — задать или посмотреть свой ключ
/styles — свои стили: добавить, переименовать, убрать
/profile — посмотреть и изменить параметры фигуры
/wardrobe — список гардероба, добавление и удаление
/wishlist — что отложено к покупке; /bought — куплено, в гардероб
/history — последние разборы
/clear — убрать переписку из чата (данные остаются)
/cancel — прервать текущий диалог
/forget — удалить все свои данные

⚠️ <b>/clear и /forget — разное.</b> /clear чистит только вид чата: сообщения исчезают, а разборы, гардероб, вишлист, стили и замеры остаются на месте. /forget стирает сами данные. Telegram разрешает боту удалять сообщения не старше 48 часов — что старше, придётся убрать вручную.

Чем полнее гардероб и параметры, тем осмысленнее пункты 5 и 15."""


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    vault: KeyVault,
    settings: Settings,
) -> None:
    await state.clear()
    user = await users_crud.get_or_create_user(
        session, message.from_user.id, settings.default_llm_provider
    )
    await message.answer(
        WELCOME, reply_markup=main_menu_kb(), disable_web_page_preview=True
    )

    # Без ключа не работает ничего, поэтому он важнее подсказки про параметры
    if not resolve_api_key(user, vault, settings).present:
        await message.answer(NO_KEY_MESSAGE, disable_web_page_preview=True)
        return

    if not user.onboarded:
        items = await wardrobe_crud.count_items(session, user.user_id)
        hint = ["Пока не знаю твоих параметров — /profile, чтобы указать."]
        if items == 0:
            hint.append("Гардероб пуст — /wardrobe, чтобы наполнить.")
        await message.answer(" ".join(hint))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("✅ Отменил.")


@router.message(Command("forget"))
async def cmd_forget(
    message: Message, command: CommandObject, session: AsyncSession, state: FSMContext
) -> None:
    """Свои данные пользователь должен уметь удалить сам — бот публичный,
    в нём лежат замеры фигуры, гардероб и API-ключ."""
    if (command.args or "").strip().lower() != "да":
        await message.answer(
            "⚠️ Удалю <b>всё</b>: параметры фигуры, гардероб, вишлист, историю разборов "
            "и сохранённый ключ. Отменить нельзя.\n\n"
            "Подтверди: <code>/forget да</code>"
        )
        return

    await state.clear()
    removed = await users_crud.delete_user(session, message.from_user.id)
    await message.answer(
        "🗑 Готово, всё удалено. /start — начать заново."
        if removed
        else "Данных о тебе и не было. /start — начать."
    )
