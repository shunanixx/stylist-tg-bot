"""Фото-путь анализа: скачивание, даунскейл, лимит, запись file_id."""

import io

import pytest
import pytest_asyncio
from PIL import Image

from db.crud import submissions as submissions_crud
from db.crud import users as users_crud
from db.database import Database
from handlers.analysis import handle_photo
from services.image_utils import MAX_SIDE
from services.llm.base import LLMResponse
from services.prompt_builder import PromptBuilder
from tests.test_analysis_flow import FULL_ANSWER, USER_ID, FakeMessage, FakeRouter


class FakePhotoSize:
    def __init__(self, file_id: str, width: int, height: int):
        self.file_id = file_id
        self.width = width
        self.height = height


class FakePhotoMessage(FakeMessage):
    def __init__(self, photos, caption=None, media_group_id=None, user_id=USER_ID):
        super().__init__(text=None, user_id=user_id)
        self.photo = photos
        self.caption = caption
        self.media_group_id = media_group_id


class FakeBot:
    """Отдаёт настоящий JPEG: downscale_image работает с реальными байтами."""

    def __init__(self, width: int = 2000, height: int = 1500):
        self._payload = _jpeg_bytes(width, height)
        self.downloaded: list[str] = []

    async def get_file(self, file_id: str):
        self.downloaded.append(file_id)
        return type("F", (), {"file_path": f"photos/{file_id}.jpg"})()

    async def download_file(self, file_path: str):
        return io.BytesIO(self._payload)


def _jpeg_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(buf, format="JPEG")
    return buf.getvalue()


def _photo_message(file_id: str = "photo-1", caption=None, media_group_id=None):
    return FakePhotoMessage(
        [FakePhotoSize(f"{file_id}-small", 90, 67), FakePhotoSize(file_id, 1280, 960)],
        caption=caption,
        media_group_id=media_group_id,
    )


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


@pytest.fixture
def builder():
    return PromptBuilder()


@pytest_asyncio.fixture
async def keyed_session(session, encrypted_key):
    """Без своего ключа фото даже не скачивается — проверка стоит раньше."""
    await users_crud.set_api_key(session, USER_ID, encrypted_key)
    return session


async def test_single_photo_is_analyzed_and_persisted(keyed_session, builder, vault):
    message = _photo_message()
    bot = FakeBot()
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER, tokens_input=900, tokens_output=400))

    await handle_photo(message, bot, keyed_session, builder, llm, vault)

    # в модель ушла картинка, а не заглушка
    assert len(llm.images[0]) == 1
    # берётся самый крупный размер из PhotoSize, не превью
    assert bot.downloaded == ["photo-1"]

    submission = (await submissions_crud.recent_submissions(keyed_session, USER_ID))[0]
    assert submission.input_type == "photo"
    assert submission.photo_file_ids == "photo-1"
    assert submission.item_title == "Куртка Carhartt"


async def test_photo_is_downscaled_before_sending(keyed_session, builder, vault):
    """Даунскейл — прямая экономия на стоимости запроса."""
    message = _photo_message()
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await handle_photo(message, FakeBot(width=3000, height=2200), keyed_session, builder, llm, vault)

    sent = Image.open(io.BytesIO(llm.images[0][0]))
    assert max(sent.size) == MAX_SIDE


async def test_caption_becomes_input_text(keyed_session, builder, vault):
    message = _photo_message(caption="Lyle&Scott, 400 грн")
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await handle_photo(message, FakeBot(), keyed_session, builder, llm, vault)

    submission = (await submissions_crud.recent_submissions(keyed_session, USER_ID))[0]
    assert submission.input_type == "photo+text"
    assert submission.input_text == "Lyle&Scott, 400 грн"


async def test_album_sent_as_one_request_with_all_photos(keyed_session, builder, vault):
    """Три фото альбома — один вызов модели, а не три."""
    import asyncio

    from handlers import analysis

    analysis._media_buffer._settle_delay = 0.01
    album = [_photo_message(f"photo-{i}", media_group_id="album-1") for i in range(3)]
    album[2].caption = "подпись на последнем"
    bot = FakeBot()
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await asyncio.gather(*(handle_photo(m, bot, keyed_session, builder, llm, vault) for m in album))

    assert len(llm.images) == 1, "альбом обязан уехать одним запросом"
    assert len(llm.images[0]) == 3
    submission = (await submissions_crud.recent_submissions(keyed_session, USER_ID))[0]
    assert submission.photo_file_ids == "photo-0\nphoto-1\nphoto-2"
    # подпись ищется по всему альбому, а не только у лидера
    assert submission.input_text == "подпись на последнем"


async def test_album_over_limit_is_rejected_before_api_call(
    keyed_session, builder, vault, monkeypatch
):
    import asyncio

    from config import settings
    from handlers import analysis

    # Владельца в этом тесте нет: лимит должен сработать
    monkeypatch.setattr(settings, "owner_user_id", 0)
    analysis._media_buffer._settle_delay = 0.01
    over = settings.max_photos_per_analysis + 1
    album = [_photo_message(f"p-{i}", media_group_id="album-2") for i in range(over)]
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await asyncio.gather(*(handle_photo(m, FakeBot(), keyed_session, builder, llm, vault) for m in album))

    assert llm.images == [], "лимит — контроль стоимости, до вызова API"
    assert "Максимум" in album[0].sent[0][0]


async def test_owner_album_ignores_the_photo_limit(
    keyed_session, builder, vault, monkeypatch
):
    """Владелец платит своей квотой — цифра из конфига его не касается."""
    import asyncio

    from config import settings
    from handlers import analysis

    monkeypatch.setattr(settings, "owner_user_id", USER_ID)
    analysis._media_buffer._settle_delay = 0.01
    over = settings.max_photos_per_analysis + 1
    album = [_photo_message(f"o-{i}", media_group_id="album-3") for i in range(over)]
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await asyncio.gather(*(handle_photo(m, FakeBot(), keyed_session, builder, llm, vault) for m in album))

    assert len(llm.images) == 1
    assert len(llm.images[0]) == over
    assert all("Максимум" not in text for text, _ in album[0].sent)


class FlakyBot(FakeBot):
    """Как FakeBot, но одно конкретное фото «не скачивается» (download_file
    вернул None) — имитирует сетевой сбой посреди альбома."""

    def __init__(self, failing_file_id: str, width: int = 2000, height: int = 1500):
        super().__init__(width=width, height=height)
        self._failing_file_id = failing_file_id

    async def download_file(self, file_path: str):
        if file_path == f"photos/{self._failing_file_id}.jpg":
            return None
        return await super().download_file(file_path)


async def test_failed_download_in_album_is_excluded_from_photo_file_ids(
    keyed_session, builder, vault
):
    """Раньше file_ids собирался до попытки скачивания: неудачно скачанное
    фото всё равно попадало в photo_file_ids, хотя модель его не видела."""
    import asyncio

    from handlers import analysis

    analysis._media_buffer._settle_delay = 0.01
    album = [_photo_message(f"flaky-{i}", media_group_id="album-flaky") for i in range(3)]
    bot = FlakyBot(failing_file_id="flaky-1")
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await asyncio.gather(
        *(handle_photo(m, bot, keyed_session, builder, llm, vault) for m in album)
    )

    # модель увидела только два реально скачанных фото
    assert len(llm.images[0]) == 2
    submission = (await submissions_crud.recent_submissions(keyed_session, USER_ID))[0]
    stored_ids = submission.photo_file_ids.split("\n")
    assert stored_ids == ["flaky-0", "flaky-2"]
    assert len(stored_ids) == len(llm.images[0])


async def test_photo_without_key_is_not_downloaded(session, builder, vault):
    """Скачивать фото до проверки ключа — расход трафика впустую."""
    message = _photo_message()
    bot = FakeBot()
    llm = FakeRouter(LLMResponse(raw_text=FULL_ANSWER))

    await handle_photo(message, bot, session, builder, llm, vault)

    assert bot.downloaded == []
    assert llm.images == []
    assert "/apikey" in message.sent[0][0]
