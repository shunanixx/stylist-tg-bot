"""Лимиты по пользователю.

Цифры в конфиге — защита от чужого расхода: бот публичный, и присланный
альбом из сорока фото или сравнение пяти моделей платятся токенами. Владельца
это не касается: он ходит своим ключом из `.env`, платит своей квотой и в наши
цифры упираться не должен.

`None` означает «без лимита» — так вызывающий код не путает отсутствие лимита
с нулём.
"""

from typing import Any

UNLIMITED = None


def is_owner(user_id: int | None, cfg: Any) -> bool:
    """`owner_user_id = 0` — владельца нет, и снимать лимиты не с кого.

    `getattr`, а не прямой доступ: в конфиг сюда приходят и урезанные объекты
    (тесты роутера), у которых поля владельца нет вовсе.
    """
    owner_id = getattr(cfg, "owner_user_id", 0) or 0
    return bool(owner_id) and user_id == owner_id


def photos_per_analysis(user_id: int | None, cfg: Any) -> int | None:
    if is_owner(user_id, cfg):
        return UNLIMITED
    return getattr(cfg, "max_photos_per_analysis", None)


def concurrent_agents(user_id: int | None, cfg: Any) -> int | None:
    if is_owner(user_id, cfg):
        return UNLIMITED
    return getattr(cfg, "max_concurrent_agents", None)


def exceeds(count: int, limit: int | None) -> bool:
    """Единая проверка: без лимита не превысить ничего."""
    return limit is not None and count > limit
