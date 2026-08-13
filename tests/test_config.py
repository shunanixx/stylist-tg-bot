"""Пути в конфиге не должны зависеть от рабочего каталога.

Бот запускают и из папки проекта, и из корня workspace (отладчик VS Code,
`python "cloth ai/bot.py"`). Относительный путь к SQLite при этом заводил
вторую пустую базу: команды отвечают, а ключи, гардероб и история пропадают.
"""

from pathlib import Path

from config import PROJECT_DIR, Settings

DB_FILE = "stylist_bot.db"


def _url(value: str) -> str:
    return Settings(database_url=value).database_url


def test_relative_sqlite_path_lands_next_to_the_project():
    assert _url(f"sqlite+aiosqlite:///./{DB_FILE}") == (
        f"sqlite+aiosqlite:///{PROJECT_DIR / DB_FILE}"
    )


def test_bare_relative_name_also_resolves():
    assert _url(f"sqlite+aiosqlite:///{DB_FILE}") == (
        f"sqlite+aiosqlite:///{PROJECT_DIR / DB_FILE}"
    )


def test_running_from_another_directory_changes_nothing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    assert _url(f"sqlite+aiosqlite:///./{DB_FILE}") == (
        f"sqlite+aiosqlite:///{PROJECT_DIR / DB_FILE}"
    )
    assert not (tmp_path / DB_FILE).exists()


def test_absolute_path_is_left_alone():
    absolute = f"sqlite+aiosqlite:////var/lib/{DB_FILE}"

    assert _url(absolute) == absolute


def test_memory_database_is_left_alone():
    assert _url("sqlite+aiosqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"


def test_other_engines_are_left_alone():
    postgres = "postgresql+asyncpg://user:pass@localhost/stylist"

    assert _url(postgres) == postgres


def test_env_file_is_read_from_the_project_directory():
    """Тот же класс поломки: относительный «.env» резолвился от cwd."""
    assert Settings.model_config["env_file"] == Path(PROJECT_DIR) / ".env"
