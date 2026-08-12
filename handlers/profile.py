from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.crud import users as users_crud
from services.measurements import BY_FIELD, MEASUREMENTS, describe, looks_like_half, parse_value
from states.onboarding_states import Onboarding

router = Router(name="profile")

# Порядок шагов FSM ↔ поля из services.measurements: вопросы, диапазоны
# и разбор «пол 42.5» живут там, чтобы не расходиться с промптом.
STEPS: dict[State, str] = {
    Onboarding.height: "height_cm",
    Onboarding.weight: "weight_kg",
    Onboarding.shoulders: "shoulders_cm",
    Onboarding.chest: "chest_cm",
    Onboarding.waist: "waist_cm",
    Onboarding.belt: "belt_cm",
}
ORDER: list[State] = list(STEPS)
SKIP_WORDS = {"-", "скип", "пропустить", "skip", "нет"}

# id последнего заданного вопроса — чтобы убрать его, когда он отвечен
PROMPT_KEY = "prompt_message_id"

SETUP_INTRO = (
    "📐 Заполним параметры — шесть чисел.\n"
    "Обхваты нужны <b>полные</b>: лентой вокруг тела, а не по плоскости вещи.\n"
    "«-» пропустить пункт, /cancel — выйти.\n\n"
)

HINT = "Полуобхват считаю сам — продавцы указывают замеры вещи именно в нём."


@router.message(Command("profile"))
async def cmd_profile(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await users_crud.get_or_create_user(session, message.from_user.id)
    await message.answer(render_profile(user) + "\n\n/setup — заполнить заново")


@router.message(Command("setup"))
async def cmd_setup(message: Message, state: FSMContext) -> None:
    first = ORDER[0]
    await state.set_state(first)
    await _ask(message, state, SETUP_INTRO + BY_FIELD[STEPS[first]].question)


@router.message(StateFilter(*ORDER), F.text)
async def process_measurement(
    message: Message, session: AsyncSession, state: FSMContext
) -> None:
    current = await state.get_state()
    step = next((s for s in ORDER if s.state == current), None)
    if step is None:  # состояние сбросили между апдейтами
        await state.clear()
        await message.answer("Диалог сброшен, начни заново: /setup")
        return

    field = STEPS[step]
    measurement = BY_FIELD[field]
    text = (message.text or "").strip().lower()
    # Ответ уходит из чата сразу: в переписке остаётся ровно один живой вопрос,
    # а цифры всё равно видны в сводке параметров.
    await _delete_quietly(message)

    if text in SKIP_WORDS:
        value: float | None = None
    else:
        value = parse_value(text, measurement)
        if value is None:
            if not measurement.girth and looks_like_half(text):
                complaint = (
                    f"«{measurement.label}» — это не обхват, полуобхвата у него нет. "
                    "Пришли обычное число."
                )
            else:
                tail = " Полуобхват — «пол 48»." if measurement.girth else ""
                complaint = f"Нужно число. Или «-», чтобы пропустить.{tail}"
            await _ask(message, state, f"⚠️ {complaint}\n\n{measurement.question}")
            return
        if not measurement.low <= value <= measurement.high:
            await _ask(
                message,
                state,
                f"⚠️ Ожидаю полный обхват от {measurement.low:g} до "
                f"{measurement.high:g} {measurement.unit}. "
                f"Если это полуобхват — пришли «пол {text}».\n\n{measurement.question}",
            )
            return

    await users_crud.update_measurements(session, message.from_user.id, **{field: value})

    index = ORDER.index(step)
    if index + 1 < len(ORDER):
        next_step = ORDER[index + 1]
        await state.set_state(next_step)
        await _ask(message, state, BY_FIELD[STEPS[next_step]].question)
        return

    # Последний вопрос убираем до clear(): в state лежит его message_id
    await _drop_prompt(message, state)
    await state.clear()
    await users_crud.set_onboarded(session, message.from_user.id)
    user = await users_crud.get_or_create_user(session, message.from_user.id)
    await message.answer("✅ Готово, параметры сохранены.\n\n" + render_profile(user))


def render_profile(user) -> str:
    """Сводка замеров: она же ответ на /profile, она же итог онбординга —
    пользователь видит все шесть чисел сразу, а не по одному в переписке."""
    lines = []
    for measurement in MEASUREMENTS:
        value = getattr(user, measurement.field, None)
        lines.append(
            describe(measurement, value)
            if value is not None
            else f"{measurement.label}: —"
        )
    return "📐 <b>Параметры фигуры</b>\n" + "\n".join(lines) + f"\n\n{HINT}"


async def _ask(message: Message, state: FSMContext, text: str) -> None:
    """Задаёт вопрос вместо предыдущего: старый удаляется, новый запоминается."""
    await _drop_prompt(message, state)
    sent = await message.answer(text)
    await state.update_data(**{PROMPT_KEY: getattr(sent, "message_id", None)})


async def _drop_prompt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_id = data.get(PROMPT_KEY)
    if not prompt_id:
        return
    bot = getattr(message, "bot", None)
    chat = getattr(message, "chat", None)
    if bot is None or chat is None:
        return
    try:
        await bot.delete_message(chat.id, prompt_id)
    except TelegramBadRequest:
        # Старше 48 часов или уже удалено вручную — не повод ронять диалог
        pass


async def _delete_quietly(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        # В группе без прав администратора чужое сообщение не удалить
        pass
