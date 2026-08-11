import asyncio

from aiogram.types import Message


class MediaGroupBuffer:
    """Собирает альбом из отдельных апдейтов.

    Telegram доставляет каждое фото media_group как самостоятельное сообщение,
    поэтому наивный хендлер отправил бы N запросов к модели вместо одного.
    Первое сообщение группы становится «лидером»: ждёт, пока группа перестанет
    расти, и забирает её целиком. Остальные получают None и молча выходят.
    """

    # Одной стабильной проверки мало: между закрытием группы и приходом
    # опоздавшего фото оно не найдёт группу, станет новым лидером и уедет
    # вторым запросом к модели. Два интервала подряд без роста закрывают окно
    # до 2 × settle_delay — с запасом от джиттера доставки Telegram.
    STABLE_CHECKS = 2

    def __init__(self, settle_delay: float = 1.0) -> None:
        self._settle_delay = settle_delay
        self._groups: dict[str, list[Message]] = {}
        self._lock = asyncio.Lock()

    async def collect(self, message: Message) -> list[Message] | None:
        group_id = message.media_group_id
        if group_id is None:
            return [message]

        async with self._lock:
            is_leader = group_id not in self._groups
            self._groups.setdefault(group_id, []).append(message)
        if not is_leader:
            return None

        # Ждём не фиксированный интервал, а стабилизации: у больших альбомов
        # на медленной сети хвост доезжает позже одной секунды.
        previous = -1
        stable = 0
        while stable < self.STABLE_CHECKS:
            await asyncio.sleep(self._settle_delay)
            async with self._lock:
                current = len(self._groups.get(group_id, []))
            stable = stable + 1 if current == previous else 0
            previous = current

        async with self._lock:
            return self._groups.pop(group_id, [])
