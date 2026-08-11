import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from db.crud import users as users_crud
from services.api_keys import NO_KEY_MESSAGE, resolve_api_key
from services.crypto import KeyVault, mask
from services.llm.gemini_provider import GeminiProvider

logger = logging.getLogger(__name__)
router = Router(name="apikey")

# Формат ключа не проверяем по префиксу: AI Studio выдаёт и «AIza…», и «AQ.…»,
# список меняется со временем. Отсекаем только явный мусор — фразы, пробелы,
# короткие строки — а годность решает живой probe_key.
_MIN_KEY_LENGTH = 20

AISTUDIO_LINK = '<a href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a>'


@router.message(Command("apikey"))
async def cmd_apikey(
    message: Message,
    session: AsyncSession,
    vault: KeyVault,
    settings: Settings,
) -> None:
    # Аргумент берём из текста, а не из CommandObject: хендлер вызывается
    # и из тестов, и после /start, где фильтр команды не отрабатывал.
    parts = (message.text or "").split(maxsplit=1)
    raw = parts[1].strip() if len(parts) > 1 else ""
    user = await users_crud.get_or_create_user(session, message.from_user.id)

    if not raw:
        await _show_status(message, user, vault, settings)
        return

    # Ключ пришёл открытым текстом и осядет в истории чата — убираем сразу,
    # ещё до проверок: даже неподходящий ключ может быть чужим рабочим.
    await _delete_message(message)

    if not _looks_like_key(raw):
        await message.answer(
            "Это не похоже на ключ Gemini — он длиннее и без пробелов. "
            f"Скопируй строку целиком с {AISTUDIO_LINK}.",
            disable_web_page_preview=True,
        )
        return

    status = await message.answer("Проверяю ключ…")
    error = await probe_key(raw, settings)
    if error is not None:
        await status.answer(
            f"❌ Ключ не принят: {error}\n\nПроверь ключ на {AISTUDIO_LINK}.",
            disable_web_page_preview=True,
        )
        return

    await users_crud.set_api_key(session, message.from_user.id, vault.encrypt(raw))
    await status.answer(
        f"✅ Ключ сохранён: <code>{mask(raw)}</code>\n"
        "Сообщение с ключом удалил.\n\n"
        "Теперь задай свои стили — по ним пойдёт разбор:\n"
        "<code>/style_add минимализм, casual</code>\n"
        "Дальше замеры — /profile. Убрать ключ — /apikey_off"
    )


@router.message(Command("apikey_off"))
async def cmd_apikey_off(
    message: Message, session: AsyncSession, vault: KeyVault, settings: Settings
) -> None:
    user = await users_crud.get_or_create_user(session, message.from_user.id)
    if not vault.decrypt(user.google_api_key_enc):
        await message.answer("Своего ключа и не было.")
        return
    await users_crud.set_api_key(session, message.from_user.id, None)
    await message.answer("Ключ удалён. Разборы не заработают, пока не пришлёшь новый.")


async def _show_status(
    message: Message, user, vault: KeyVault, settings: Settings
) -> None:
    source = resolve_api_key(user, vault, settings)
    if not source.present:
        await message.answer(NO_KEY_MESSAGE, disable_web_page_preview=True)
        return
    if not source.is_own:
        await message.answer(
            "Ты владелец — работает ключ из .env. "
            "Свой личный: <code>/apikey ВСТАВЬ_КЛЮЧ</code>"
        )
        return
    await message.answer(
        f"🔑 Ключ сохранён: <code>{mask(source.api_key)}</code>\n"
        "Заменить — пришли новый, убрать — /apikey_off"
    )


def _looks_like_key(value: str) -> bool:
    """Дешёвый отсев опечаток до сетевого вызова: ключ — одно длинное слово."""
    return len(value) >= _MIN_KEY_LENGTH and " " not in value


async def probe_key(api_key: str, settings: Settings) -> str | None:
    """Живая проверка ключа. None — ключ рабочий.

    Без неё неверный ключ всплыл бы только на первом разборе, когда человек
    уже написал длинное описание вещи. count_tokens не тратит квоту, но
    требует валидной авторизации.
    """
    try:
        provider = GeminiProvider(api_key=api_key, model=settings.gemini_model)
        await provider.count_tokens("ping")
    except Exception as exc:  # SDK бросает свои типы ошибок
        return _humanize(exc)
    return None


def _humanize(exc: Exception) -> str:
    text = str(exc)
    if "API_KEY_INVALID" in text or "API key not valid" in text:
        return "Google говорит, что ключ недействителен"
    if "PERMISSION_DENIED" in text or "403" in text:
        return "у ключа нет доступа к Gemini API"
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "квота ключа уже исчерпана"
    # Сырой текст SDK не показываем: там бывает URL с параметрами запроса
    logger.warning("Проверка ключа не удалась: %s", type(exc).__name__)
    return "не удалось проверить, попробуй ещё раз"


async def _delete_message(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        # В группе без прав администратора чужое сообщение не удалить
        await message.answer(
            "⚠️ Не смог удалить сообщение с ключом — удали его сам, "
            "ключ виден в истории чата."
        )
