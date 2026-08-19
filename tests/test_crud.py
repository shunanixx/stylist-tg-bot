import pytest
import pytest_asyncio

from db.crud import submissions as submissions_crud
from db.crud import users as users_crud
from db.crud import wardrobe as wardrobe_crud
from db.database import Database

USER_ID = 123456789


@pytest_asyncio.fixture
async def session():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema()
    async with database.session() as session:
        yield session
    await database.dispose()


async def test_get_or_create_is_idempotent(session):
    first = await users_crud.get_or_create_user(session, USER_ID)
    second = await users_crud.get_or_create_user(session, USER_ID)

    assert first is second
    assert len(await users_crud.list_users(session)) == 1


async def test_update_measurements_keeps_fractions(session):
    user = await users_crud.update_measurements(
        session, USER_ID, height_cm=175, belt_cm=42.5
    )

    assert user.height_cm == 175
    assert user.belt_cm == 42.5


async def test_update_measurements_rejects_unknown_field(session):
    with pytest.raises(ValueError, match="Неизвестные поля"):
        await users_crud.update_measurements(session, USER_ID, inseam_cm=80)


async def test_wardrobe_soft_delete_hides_item_but_keeps_row(session):
    await users_crud.get_or_create_user(session, USER_ID)
    item = await wardrobe_crud.add_item(session, USER_ID, "Серый свитшот", size="S")

    await wardrobe_crud.deactivate_item(session, USER_ID, item.id)

    assert await wardrobe_crud.list_items(session, USER_ID) == []
    assert len(await wardrobe_crud.list_items(session, USER_ID, active_only=False)) == 1


async def test_wardrobe_deactivate_ignores_other_users_item(session):
    await users_crud.get_or_create_user(session, USER_ID)
    item = await wardrobe_crud.add_item(session, USER_ID, "Кеды")

    assert await wardrobe_crud.deactivate_item(session, 999, item.id) is None
    assert len(await wardrobe_crud.list_items(session, USER_ID)) == 1


async def test_submission_flow_stores_result_and_meta(session):
    await users_crud.get_or_create_user(session, USER_ID)
    submission = await submissions_crud.create_submission(
        session, USER_ID, input_type="text", input_text="куртка Carhartt"
    )
    await submissions_crud.add_result(
        session,
        submission_id=submission.id,
        provider="gemini",
        verdict="брать",
        full_response="разбор",
        raw_response="разбор\n===DATA===\n{}",
        tokens_input=1200,
        tokens_output=900,
        latency_ms=4300,
    )
    await submissions_crud.set_item_meta(
        session,
        submission_id=submission.id,
        user_id=USER_ID,
        item_title="Куртка Carhartt",
        item_category="верхняя одежда",
        final_verdict="брать",
    )

    results = await submissions_crud.results_for(session, submission.id)
    assert [r.provider for r in results] == ["gemini"]
    assert results[0].tokens_input == 1200

    stored = await submissions_crud.get_submission(session, USER_ID, submission.id)
    assert stored.item_title == "Куртка Carhartt"
    assert stored.final_verdict == "брать"


async def test_set_item_meta_refuses_a_submission_belonging_to_another_user(session):
    """Как get_submission: чужой submission_id не должен молча перезаписаться,
    например из-за гонки между двумя параллельными разборами."""
    OTHER_USER_ID = USER_ID + 1
    await users_crud.get_or_create_user(session, USER_ID)
    submission = await submissions_crud.create_submission(
        session, USER_ID, input_type="text", input_text="куртка Carhartt"
    )

    result = await submissions_crud.set_item_meta(
        session,
        submission_id=submission.id,
        user_id=OTHER_USER_ID,
        item_title="Чужое название",
        item_category="верх",
        final_verdict="брать",
    )

    assert result is None
    stored = await submissions_crud.get_submission(session, USER_ID, submission.id)
    assert stored.item_title is None


async def test_multiple_results_per_submission(session):
    """Сравнение моделей: несколько записей на один submission."""
    await users_crud.get_or_create_user(session, USER_ID)
    submission = await submissions_crud.create_submission(session, USER_ID, "text", "джинсы")

    for provider, verdict in (("gemini", "брать"), ("deepseek", "не брать")):
        await submissions_crud.add_result(
            session, submission.id, provider, verdict, "текст", "сырой"
        )

    results = await submissions_crud.results_for(session, submission.id)
    assert [(r.provider, r.verdict) for r in results] == [
        ("gemini", "брать"),
        ("deepseek", "не брать"),
    ]


async def test_recent_submissions_skips_unanalyzed_and_is_chronological(session):
    await users_crud.get_or_create_user(session, USER_ID)
    for title in ("Худи", "Кеды", "Джинсы"):
        submission = await submissions_crud.create_submission(session, USER_ID, "text", title)
        await submissions_crud.set_item_meta(session, submission.id, USER_ID, title, None, "брать")
    await submissions_crud.create_submission(session, USER_ID, "text", "без разбора")

    recent = await submissions_crud.recent_submissions(session, USER_ID, limit=2)

    assert [s.item_title for s in recent] == ["Кеды", "Джинсы"]


async def test_get_submission_scoped_to_user(session):
    await users_crud.get_or_create_user(session, USER_ID)
    submission = await submissions_crud.create_submission(session, USER_ID, "text", "худи")

    assert await submissions_crud.get_submission(session, 999, submission.id) is None
