"""A bot token must not inherit its owner's instance-admin identity on /Join.

``_session_admin_id`` mapped ``current_user.user_id`` straight onto the shared
``admin_users.id`` space. A bot token's ``user_id`` is its *human owner*, so a
bot owned by an admin could pass the ``admin.plexijoin.manage`` gate derived
from the owner's admin row and read/mutate federation connections with the
owner's full authority. The credential's own (restricted) permission map was
never consulted on this surface.

These tests pin the fail-closed gate: bot credentials are rejected before any
admin lookup, while a genuine user token still resolves and is scope-checked.
"""

import pytest

import src.api.routes.join as join_module
from src.api.routes.join import JoinApiError
from src.core.auth.models import AccountType, TokenInfo

ADMIN_ID = 424242
BOT_ID = 999999


def _token(token_type: str) -> TokenInfo:
    is_bot = token_type == "bot"
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=BOT_ID if is_bot else ADMIN_ID,
        user_id=ADMIN_ID,  # bots carry their owner's user_id
        session_id=None if is_bot else 1,
        permissions={},
        rate_limit_tier="bot" if is_bot else "standard",
        expires_at=None,
        username="owner" if not is_bot else "owner_bot",
        account_type=AccountType.BOT if is_bot else AccountType.USER,
    )


class _RecordingDB:
    """Admin lookup that would grant the owner's identity if reached."""

    def __init__(self):
        self.admin_lookups = []

    def fetch_one(self, query, params=None):
        self.admin_lookups.append((query, params))
        return {"id": ADMIN_ID}


@pytest.fixture
def env(monkeypatch):
    db = _RecordingDB()
    calls = {"permission": []}

    monkeypatch.setattr(join_module, "_get_db", lambda: db, raising=False)
    monkeypatch.setattr("src.api.get_db", lambda: db)
    monkeypatch.setattr(
        "src.api.routes.admin.utils.check_host_restriction",
        lambda request, require_admin_ui=False: None,
    )

    def _check(admin_id, permission, db_arg=None):
        calls["permission"].append((admin_id, permission))
        return True  # the owner really is a scoped instance admin

    monkeypatch.setattr("src.core.admin.permissions.check_admin_permission", _check)
    return db, calls


def test_bot_token_cannot_borrow_owner_admin_identity(env):
    db, calls = env

    with pytest.raises(JoinApiError) as exc:
        join_module._require_manage(object(), _token("bot"))

    assert exc.value.status_code == 403
    # Fail closed *before* the owner's admin row is consulted or the scope is
    # checked: the bot must never get as far as using the owner's authority.
    assert db.admin_lookups == []
    assert calls["permission"] == []


def test_user_token_still_maps_to_admin_identity(env):
    db, calls = env

    admin_id = join_module._require_manage(object(), _token("user"))

    assert admin_id == ADMIN_ID
    assert len(db.admin_lookups) == 1
    assert calls["permission"] == [(ADMIN_ID, "admin.plexijoin.manage")]


class _ManagerStub:
    """If the gate is bypassed this would happily serve the listing."""

    def list_connections(self, status=None):
        return {"connections": [], "total": 0, "page": 1, "per_page": 50, "pages": 1}


@pytest.mark.asyncio
async def test_list_joins_rejects_bot_token(env, monkeypatch):
    """The real endpoint must deny a bot credential at its identity gate."""
    monkeypatch.setattr(join_module, "_get_manager", lambda request: _ManagerStub())

    with pytest.raises(JoinApiError) as exc:
        await join_module.list_joins(object(), status=None, current_user=_token("bot"))

    assert exc.value.status_code == 403
