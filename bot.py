import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import settings
from db.database import Database
from filters.owner import IsOwner
from handlers import (
    admin,
    analysis,
    api_key,
    cleanup,
    history,
    model_selection,
    profile,
    start,
    styles,
    wardrobe,
    wishlist,
)
from middlewares.chat_log import ChatLogMiddleware
from middlewares.db_session import DbSessionMiddleware
from middlewares.track_sent import TrackSentMessagesMiddleware
from services.crypto import KeyVault
from services.llm.factory import IMPLEMENTED, PLANNED
from services.llm_router import LLMRouter
from services.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Начало"),
    BotCommand(command="apikey", description="Свой ключ Gemini"),
    BotCommand(command="profile", description="Параметры фигуры"),
    BotCommand(command="styles", description="Мои стили"),
    BotCommand(command="wardrobe", description="Гардероб"),
    BotCommand(command="wishlist", description="Вишлист"),
    BotCommand(command="history", description="Журнал вещей"),
    BotCommand(command="clear", description="Очистить чат"),
    BotCommand(command="model", description="Модель для анализа"),
    BotCommand(command="help", description="Как пользоваться"),
]


def check_providers() -> None:
    """Падаем на старте, а не на первом сообщении пользователя.

    Клиент здесь не строим: ключи теперь пользовательские, проверять нечего
    до первого запроса — только состав ENABLED_PROVIDERS.
    """
    for name in settings.enabled_providers:
        if name not in IMPLEMENTED:
            stage = PLANNED.get(name, "не запланирован")
            raise SystemExit(
                f"ENABLED_PROVIDERS содержит '{name}', но провайдер не реализован "
                f"({stage}). Уберите его из .env."
            )


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    check_providers()

    database = Database(settings.database_url)
    await database.create_schema()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Перехват на уровне API-запросов: message.answer() разбросан по проекту,
    # и учитывать отправленное в каждом вызове — значит забыть в половине мест.
    bot.session.middleware(TrackSentMessagesMiddleware())

    dispatcher = Dispatcher(storage=MemoryStorage())

    # Бот публичный: сплошного owner-шлюза больше нет, приватны только
    # админ-команды — они висят на фильтре IsOwner.
    dispatcher.update.outer_middleware(DbSessionMiddleware(database))
    # Строго после сессии: журнал пишется в ту же транзакцию, иначе вторая
    # сессия конкурировала бы за блокировку SQLite.
    dispatcher.update.outer_middleware(ChatLogMiddleware())

    dispatcher["prompt_builder"] = PromptBuilder()
    dispatcher["llm_router"] = LLMRouter(settings)
    dispatcher["vault"] = KeyVault(settings.encryption_key or settings.telegram_bot_token)
    dispatcher["settings"] = settings

    admin.router.message.filter(IsOwner(settings.owner_user_id))

    dispatcher.include_routers(
        start.router,
        api_key.router,
        admin.router,
        profile.router,
        styles.router,
        wardrobe.router,
        wishlist.router,
        history.router,
        cleanup.router,
        model_selection.router,
        analysis.router,  # последним: ловит свободный текст
    )

    await bot.set_my_commands(COMMANDS)
    logger.info(
        "Бот запущен (публичный режим, ключ у каждого свой). Провайдеры: %s, модель: %s",
        ", ".join(settings.enabled_providers),
        settings.gemini_model,
    )
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
        await database.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code:
            raise
        logger.info("Остановлено")
