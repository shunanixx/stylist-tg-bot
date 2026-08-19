import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import Settings, settings
from db.database import Database
from filters.owner import IsOwner
from handlers import (
    admin,
    analysis,
    api_key,
    cleanup,
    history,
    menu,
    model_selection,
    profile,
    start,
    styles,
    wardrobe,
    wishlist,
)
from middlewares.chat_log import ChatLogMiddleware
from middlewares.db_session import DbSessionMiddleware
from middlewares.identity import IdentityMiddleware
from middlewares.track_sent import TrackSentMessagesMiddleware
from services.crypto import KeyVault
from services.llm.factory import IMPLEMENTED, PLANNED
from services.llm_router import LLMRouter
from services.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

# Хостинг дёргает его как health check, keep-alive-крон — как повод не дать
# сервису уснуть. В COMMANDS не путать: это HTTP, а не команда бота.
HEALTH_PATH = "/healthz"

COMMANDS = [
    BotCommand(command="start", description="Начало"),
    BotCommand(command="menu", description="Показать кнопки меню"),
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


def build_dispatcher(database: Database, cfg: Settings = settings) -> Dispatcher:
    """Сборка диспетчера: middleware, зависимости, порядок роутеров.

    Вынесено из main(), чтобы тест целостности проверял ту же схему, что идёт
    в прод, а не свою копию. Вызывать один раз на процесс: роутеры —
    модульные объекты, второй include их же упадёт «already attached».
    """
    dispatcher = Dispatcher(storage=MemoryStorage())

    # Бот публичный: сплошного owner-шлюза больше нет, приватны только
    # админ-команды — они висят на фильтре IsOwner.
    dispatcher.update.outer_middleware(DbSessionMiddleware(database))
    # Строго после сессии: журнал пишется в ту же транзакцию, иначе вторая
    # сессия конкурировала бы за блокировку SQLite.
    dispatcher.update.outer_middleware(ChatLogMiddleware())
    # @username Telegram присылает с каждым апдейтом и нигде не хранит —
    # без этого /numbers показывал бы одни голые id.
    dispatcher.update.outer_middleware(IdentityMiddleware(cfg.default_llm_provider))

    dispatcher["prompt_builder"] = PromptBuilder()
    dispatcher["llm_router"] = LLMRouter(cfg)
    dispatcher["vault"] = KeyVault(cfg.encryption_key or cfg.telegram_bot_token)
    dispatcher["settings"] = cfg

    admin.router.message.filter(IsOwner(cfg.owner_user_id))

    dispatcher.include_routers(
        start.router,
        api_key.router,
        admin.router,
        # До profile и analysis: кнопка присылает обычный текст, и любой роутер
        # с F.text выше перехватил бы её как ответ FSM или как описание вещи.
        menu.router,
        profile.router,
        styles.router,
        wardrobe.router,
        wishlist.router,
        history.router,
        cleanup.router,
        model_selection.router,
        analysis.router,  # последним: ловит свободный текст
    )
    return dispatcher


async def health(_: web.Request) -> web.Response:
    """Живость процесса — без обращения к БД.

    Соблазн проверять здесь и базу есть, но её дёргают каждые несколько минут
    health check хостинга и keep-alive-крон: бесплатный serverless-Postgres
    при этом не заснёт никогда и сожжёт месячную квоту (см. engine_options).
    Падение БД видно в логах разбора, а не в пинге.
    """
    return web.Response(text="ok")


def build_web_app(bot: Bot, dispatcher: Dispatcher, cfg: Settings = settings) -> web.Application:
    """HTTP-приложение вебхука: aiohttp, а не новый фреймворк.

    aiohttp уже стоит — на нём работает сам aiogram, и его же обёртка
    (SimpleRequestHandler) умеет проверять секретный заголовок Telegram.
    Заводить ради одного POST-роута FastAPI значило бы тянуть второй сервер
    в тот же процесс.
    """
    app = web.Application()
    app.router.add_get(HEALTH_PATH, health)
    # handle_in_background по умолчанию: Telegram получает 200 сразу, а разбор
    # (13 с у модели) идёт своей задачей. Иначе долгий ответ выглядит для
    # Telegram как таймаут, и он присылает тот же апдейт заново.
    SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=cfg.resolved_webhook_secret,
    ).register(app, path=cfg.webhook_path)
    setup_application(app, dispatcher, bot=bot)
    return app


async def run_webhook(bot: Bot, dispatcher: Dispatcher, cfg: Settings = settings) -> None:
    # Сервер поднимаем до set_webhook: иначе Telegram может прислать первый
    # апдейт раньше, чем TCPSite начнёт слушать порт, и тот апдейт потеряется
    # (или задержится до следующей попытки Telegram) сразу после каждого деплоя.
    runner = web.AppRunner(build_web_app(bot, dispatcher, cfg))
    await runner.setup()
    site = web.TCPSite(runner, host=cfg.webhook_host, port=cfg.port)
    await site.start()
    await bot.set_webhook(
        url=cfg.webhook_url,
        secret_token=cfg.resolved_webhook_secret,
        drop_pending_updates=True,
        # Только то, на что есть хендлеры: остальное Telegram даже не отправит.
        allowed_updates=dispatcher.resolve_used_update_types(),
    )
    logger.info(
        "Вебхук: слушаю %s:%s, путь %s, health %s",
        cfg.webhook_host,
        cfg.port,
        cfg.webhook_path,
        HEALTH_PATH,
    )
    try:
        # Апдейты обрабатывает aiohttp; процессу остаётся не завершиться.
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


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

    dispatcher = build_dispatcher(database)

    await bot.set_my_commands(COMMANDS)
    logger.info(
        "Бот запущен (%s, публичный режим, ключ у каждого свой). Провайдеры: %s, модель: %s",
        settings.run_mode,
        ", ".join(settings.enabled_providers),
        settings.gemini_model,
    )
    try:
        if settings.run_mode == "webhook":
            await run_webhook(bot, dispatcher)
        else:
            # Вебхук и polling взаимно исключают друг друга: пока он установлен,
            # getUpdates отвечает 409.
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
