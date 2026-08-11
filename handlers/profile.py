from aiogram import F, Router
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


@router.message(Command("profile"))
async def cmd_profile(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await users_crud.get_or_create_user(session, message.from_user.id)

    lines = []
    for measurement in MEASUREMENTS:
        value = getattr(user, measurement.field)
        shown = describe(measurement, value) if value is not None else f"{measurement.label}: —"
        lines.append(shown)

    await message.answer(
        "<b>Параметры фигуры</b>\n"
        + "\n".join(lines)
        + "\n\nПолуобхват считается сам — продавцы указывают замеры вещи именно в нём."
        + "\n/setup — заполнить заново"
    )


@router.message(Command("setup"))
async def cmd_setup(message: Message, state: FSMContext) -> None:
    first = ORDER[0]
    await state.set_state(first)
    await message.answer(
        "Заполним параметры. «-» чтобы пропустить пункт, /cancel — выйти.\n"
        "Обхваты меряй лентой вокруг; если под рукой только вещь по плоскости — "
        "пиши полуобхват как «пол 48».\n\n"
        f"{BY_FIELD[STEPS[first]].question}"
    )


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

    if text in SKIP_WORDS:
        value: float | None = None
    else:
        value = parse_value(text, measurement)
        if value is None:
            if not measurement.girth and looks_like_half(text):
                await message.answer(
                    f"«{measurement.label}» — это не обхват, полуобхвата у него нет. "
                    "Пришли обычное число."
                )
            else:
                hint = " Полуобхват — «пол 48»." if measurement.girth else ""
                await message.answer(f"Нужно число. Или «-», чтобы пропустить.{hint}")
            return
        if not measurement.low <= value <= measurement.high:
            await message.answer(
                f"Ожидаю обхват от {measurement.low:g} до {measurement.high:g} "
                f"{measurement.unit}. Если это полуобхват — пришли «пол {text}»."
            )
            return

    await users_crud.update_measurements(session, message.from_user.id, **{field: value})

    index = ORDER.index(step)
    if index + 1 < len(ORDER):
        next_step = ORDER[index + 1]
        await state.set_state(next_step)
        await message.answer(BY_FIELD[STEPS[next_step]].question)
        return

    await state.clear()
    await users_crud.set_onboarded(session, message.from_user.id)
    await message.answer("Готово. Параметры сохранены — /profile посмотреть.")
