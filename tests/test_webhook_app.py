"""HTTP-приложение вебхука: health check, секрет, доставка апдейта.

На free-тарифах бот получает апдейты через этот роут, и сломать его нечем,
кроме деплоя: локально всё ходит через polling. Поэтому проверяем настоящее
приложение из `build_web_app` настоящими HTTP-запросами.
"""

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp.test_utils import TestClient, TestServer

from bot import HEALTH_PATH, build_web_app
from config import Settings

TOKEN = "123456789:AAHfake-token-for-webhook-tests"
CHAT_ID = 4242
USER_ID = 555


@pytest.fixture
def cfg() -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token=TOKEN,
        run_mode="webhook",
        webhook_base_url="https://bot.example.com",
        webhook_secret="fake-webhook-secret",
    )


@pytest.fixture
def handled() -> list[str]:
    return []


@pytest_asyncio.fixture
async def client(cfg, handled):
    dispatcher = Dispatcher()

    @dispatcher.message(F.text)
    async def record(message):
        handled.append(message.text)

    # Сессия настоящая, но к сети не ходит: хендлер ничего не отправляет,
    # а build_web_app сам по себе не вызывает Telegram API.
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    app = build_web_app(bot, dispatcher, cfg)
    test_client = TestClient(TestServer(app))
    await test_client.start_server()
    yield test_client
    await test_client.close()


def _update(update_id: int = 1, text: str = "джинсы") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": CHAT_ID, "type": "private"},
            "from": {"id": USER_ID, "is_bot": False, "first_name": "Кирилл"},
            "text": text,
        },
    }


async def _wait_for(condition, timeout: float = 2.0) -> bool:
    """Апдейт разбирается фоновой задачей: ответ 200 приходит раньше хендлера."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_health_check_answers_ok(client):
    """По нему хостинг решает, жив ли сервис."""
    response = await client.get(HEALTH_PATH)

    assert response.status == 200
    assert await response.text() == "ok"


async def test_health_check_needs_no_database(client, cfg):
    """Пинг ходит каждые несколько минут: запрос к БД не давал бы ей заснуть."""
    for _ in range(3):
        assert (await client.get(HEALTH_PATH)).status == 200


async def test_update_with_the_secret_reaches_the_handler(client, cfg, handled):
    response = await client.post(
        cfg.webhook_path,
        json=_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": cfg.resolved_webhook_secret},
    )

    assert response.status == 200
    assert await _wait_for(lambda: handled == ["джинсы"]), handled


async def test_update_without_the_secret_is_rejected(client, cfg, handled):
    """Путь вебхука видно в логах прокси — на нём держится только секрет."""
    response = await client.post(cfg.webhook_path, json=_update())

    assert response.status == 401
    assert not await _wait_for(lambda: bool(handled), timeout=0.2)


async def test_update_with_a_wrong_secret_is_rejected(client, cfg, handled):
    response = await client.post(
        cfg.webhook_path,
        json=_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "fake-wrong-secret"},
    )

    assert response.status == 401
    assert not handled


async def test_webhook_path_is_not_the_root(client, cfg):
    """Корень отдан платформе: по нему health check проходил бы и с мёртвым ботом."""
    assert cfg.webhook_path != "/"
    assert (await client.post("/", json=_update())).status == 404


async def test_health_path_does_not_accept_updates(client):
    assert (await client.post(HEALTH_PATH, json=_update())).status == 405
