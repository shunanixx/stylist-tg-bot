from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.crud import users as users_crud
from keyboards.model_kb import SELECT_MODEL_PREFIX, model_select_kb

router = Router(name="model_selection")


@router.message(Command("model"))
async def cmd_model(
    message: Message, session: AsyncSession, state: FSMContext | None = None
) -> None:
    # Команда посреди чужого FSM-диалога обязана прервать его — иначе
    # следующий текст пользователя уйдёт в тот незакрытый ввод.
    if state is not None and await state.get_state() is not None:
        await state.clear()
        await message.answer("Ввод прерван — вернулся в меню.")
    user = await users_crud.get_or_create_user(session, message.from_user.id)
    providers = settings.enabled_providers
    if len(providers) == 1:
        await message.answer(
            f"🤖 Анализ идёт через <b>{providers[0]}</b> — модель "
            f"<code>{settings.gemini_model}</code>.\n"
            "Другие провайдеры появятся на следующих этапах."
        )
        return
    await message.answer(
        "🤖 Выбери модель для анализа:",
        reply_markup=model_select_kb(providers, user.default_llm_provider),
    )


@router.callback_query(F.data.startswith(f"{SELECT_MODEL_PREFIX}:"))
async def set_model(callback: CallbackQuery, session: AsyncSession) -> None:
    provider = callback.data.rsplit(":", 1)[1]
    if provider not in settings.enabled_providers:
        await callback.answer("Этот провайдер отключён.", show_alert=True)
        return
    await users_crud.set_default_provider(session, callback.from_user.id, provider)
    await callback.answer(f"Теперь анализирует {provider}")
    if callback.message is not None:
        await callback.message.edit_reply_markup(
            reply_markup=model_select_kb(settings.enabled_providers, provider)
        )
