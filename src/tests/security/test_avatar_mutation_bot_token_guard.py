"""Avatar ``@me`` mutations must reject bot credentials.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id is
in ``account_id``).  ``POST/DELETE /avatars/users/@me`` both key the avatar
store on ``current_user.user_id``, so without an identity gate a bot token can
overwrite or delete its owner's avatar.  The sibling user-router mixin
(``users/avatar.py``) already calls ``require_user_token``; these two handlers
were the remaining account-bound gap.

The runtime tests are non-vacuous: before the guard, the recorder shows the
bot token reaching ``get_avatars``; after the guard it is refused with 403 and
the manager is never touched.  A control test proves the recorder fires for a
human token, so an empty recorder is meaningful.

Run with::

    pytest src/tests/security/test_avatar_mutation_bot_token_guard.py -q -n 0
"""

import inspect
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes.avatars import routes as avatar_routes
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="bot" if token_type == "bot" else "standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


# Bot 500 is owned by human 100.  Without the guard the handler resolves the
# owner's identity and mutates user 100's avatar using the bot's credential.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")


@pytest.fixture
def manager_recorder(monkeypatch):
    """Record accesses to the avatar manager; fail loudly if a bot reaches it."""
    calls = []

    def _get_avatars():
        calls.append("get_avatars")
        manager = Mock()
        manager.delete_user_avatar.return_value = True
        return manager

    monkeypatch.setattr(avatar_routes.api, "get_avatars", _get_avatars)
    monkeypatch.setattr(
        avatar_routes, "_invalidate_user_avatar_cache", lambda _uid: None
    )
    return calls


def _run(handler, **kwargs):
    result = handler(**kwargs)
    if inspect.isawaitable(result):
        import asyncio

        return asyncio.run(result)
    return result


def test_upload_my_avatar_rejects_bot_token_before_manager_access(manager_recorder):
    with pytest.raises(HTTPException) as exc:
        _run(
            avatar_routes.upload_my_avatar,
            file=Mock(content_type=None),
            current_user=_BOT,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    assert manager_recorder == []


def test_delete_my_avatar_rejects_bot_token_before_manager_access(manager_recorder):
    with pytest.raises(HTTPException) as exc:
        _run(avatar_routes.delete_my_avatar, current_user=_BOT)

    assert exc.value.status_code == 403
    assert manager_recorder == []


def test_human_token_still_reaches_the_avatar_manager(manager_recorder):
    """Control: a real user session passes the gate and uses the manager.

    Without this, an empty ``manager_recorder`` in the bot tests could pass for
    a vacuous reason (e.g. an unrelated early return).
    """
    with pytest.raises(HTTPException) as exc:
        _run(
            avatar_routes.upload_my_avatar,
            file=Mock(content_type=None),
            current_user=_USER,
        )

    assert exc.value.status_code == 400  # invalid file, reached validation
    assert manager_recorder == ["get_avatars"]


def test_both_handlers_call_require_user_token():
    """Static guard so a future edit cannot silently drop the runtime check."""
    for handler in (avatar_routes.upload_my_avatar, avatar_routes.delete_my_avatar):
        assert "require_user_token(current_user)" in inspect.getsource(handler)
