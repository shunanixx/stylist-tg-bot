"""Сборка альбома из отдельных апдейтов: один запрос к модели, а не N."""

import asyncio

from services.media_group_buffer import MediaGroupBuffer

FAST = 0.01  # тестам ни к чему реальная секунда ожидания


class FakeMessage:
    def __init__(self, media_group_id: str | None, caption: str | None = None):
        self.media_group_id = media_group_id
        self.caption = caption


async def test_single_photo_passes_through_immediately():
    buffer = MediaGroupBuffer(settle_delay=FAST)
    message = FakeMessage(None)

    assert await buffer.collect(message) == [message]


async def test_album_collected_once_by_leader():
    buffer = MediaGroupBuffer(settle_delay=FAST)
    album = [FakeMessage("grp-1") for _ in range(3)]

    results = await asyncio.gather(*(buffer.collect(m) for m in album))

    collected = [r for r in results if r is not None]
    assert len(collected) == 1, "альбом должен забрать ровно один хендлер"
    assert collected[0] == album
    assert results.count(None) == 2


async def test_late_photo_still_joins_album():
    """Хвост альбома на медленной сети доезжает позже первого интервала."""
    buffer = MediaGroupBuffer(settle_delay=FAST)
    first = FakeMessage("grp-2")
    late = FakeMessage("grp-2")

    async def send_late():
        await asyncio.sleep(FAST * 1.5)
        return await buffer.collect(late)

    leader_result, late_result = await asyncio.gather(buffer.collect(first), send_late())

    assert late_result is None
    assert leader_result == [first, late]


async def test_groups_do_not_leak_between_albums():
    buffer = MediaGroupBuffer(settle_delay=FAST)
    first = [FakeMessage("grp-3") for _ in range(2)]
    second = [FakeMessage("grp-4") for _ in range(2)]

    batch_one = await asyncio.gather(*(buffer.collect(m) for m in first))
    batch_two = await asyncio.gather(*(buffer.collect(m) for m in second))

    assert [r for r in batch_one if r][0] == first
    assert [r for r in batch_two if r][0] == second
    assert buffer._groups == {}, "буфер обязан очищаться, иначе течёт память"
