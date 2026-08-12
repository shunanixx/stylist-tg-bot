"""Лимиты и снятие их с владельца.

Проверяем то, что легко разъезжается: владелец без лимитов, остальные с
лимитами, а `owner_user_id = 0` не делает безлимитным первого попавшегося.
"""

from types import SimpleNamespace

from services import limits

OWNER = 42


def _cfg(owner_user_id=OWNER, photos=10, agents=2):
    return SimpleNamespace(
        owner_user_id=owner_user_id,
        max_photos_per_analysis=photos,
        max_concurrent_agents=agents,
    )


def test_owner_has_no_photo_or_agent_limit():
    cfg = _cfg()

    assert limits.photos_per_analysis(OWNER, cfg) is limits.UNLIMITED
    assert limits.concurrent_agents(OWNER, cfg) is limits.UNLIMITED


def test_everyone_else_keeps_the_configured_numbers():
    cfg = _cfg()

    assert limits.photos_per_analysis(7, cfg) == 10
    assert limits.concurrent_agents(7, cfg) == 2


def test_no_owner_configured_exempts_nobody():
    """`OWNER_USER_ID` не задан — снимать лимиты не с кого, в том числе с id 0."""
    cfg = _cfg(owner_user_id=0)

    assert limits.is_owner(0, cfg) is False
    assert limits.is_owner(None, cfg) is False
    assert limits.photos_per_analysis(0, cfg) == 10


def test_anonymous_user_is_not_the_owner():
    assert limits.is_owner(None, _cfg()) is False


def test_missing_owner_field_does_not_crash():
    """В роутер и тесты приходят урезанные конфиги без поля владельца."""
    cfg = SimpleNamespace(max_concurrent_agents=2)

    assert limits.is_owner(OWNER, cfg) is False
    assert limits.concurrent_agents(OWNER, cfg) == 2


def test_without_a_limit_nothing_exceeds_it():
    assert limits.exceeds(1000, limits.UNLIMITED) is False
    assert limits.exceeds(11, 10) is True
    assert limits.exceeds(10, 10) is False
