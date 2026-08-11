import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# config.Settings читает .env при импорте — тесты не должны зависеть от него
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OWNER_USER_ID", "1")
# Ключа в окружении нет намеренно: бот публичный, ключ приносит пользователь.
# Тесты, которым нужен ключ владельца, задают его сами.
os.environ.pop("GOOGLE_API_KEY", None)

TEST_API_KEY = "AIza-test-user-key"


@pytest.fixture
def vault():
    from services.crypto import KeyVault

    return KeyVault("test-secret")


@pytest.fixture
def encrypted_key(vault):
    return vault.encrypt(TEST_API_KEY)
