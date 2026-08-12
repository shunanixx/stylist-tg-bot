"""Нумерация вещей в списках.

Пользователю показывается позиция в списке (1, 2, 3…), а не `id` из БД.
`id` — сквозной автоинкремент: после удаления в списке появлялись дыры
(1, 3, 4), а новая вещь получала следующий глобальный номер вместо
следующего по списку. Позиция всегда плотная и совпадает с тем, что человек
видит на экране.

Обратная сторона: номер вещи меняется, когда удаляют предыдущую. Для команд
это нормально — их набирают, глядя в свежий список.
"""

from typing import Any, TypeVar

T = TypeVar("T")


def resolve_position(items: list[T], raw: str) -> T | None:
    """Номер из списка → сама вещь. None, если номер не число или вне списка."""
    if not raw.isdigit():
        return None
    position = int(raw)
    if not 1 <= position <= len(items):
        return None
    return items[position - 1]


def position_of(items: list[Any], item: Any) -> int | None:
    """Позиция вещи в списке — чтобы показать номер сразу после добавления."""
    for index, candidate in enumerate(items, start=1):
        if getattr(candidate, "id", None) == getattr(item, "id", None):
            return index
    return None


def by_id(items: list[T], item_id: int | None) -> T | None:
    """Вещь по `id` — для инлайн-кнопок: в них зашит id, а не позиция.

    Ищем в свежем списке, а не в БД: кнопка под старым сообщением может
    указывать на уже удалённую вещь, и промах должен выглядеть как «её уже
    нет», а не как повторное удаление.
    """
    if item_id is None:
        return None
    for candidate in items:
        if getattr(candidate, "id", None) == item_id:
            return candidate
    return None
