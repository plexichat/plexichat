"""Voice moderation owner gate must reject a malformed owner row.

``ModerationMixin._require_can_moderate_member`` derives ``is_owner`` from
``int(owner_id) == int(moderator_id)`` and ``target_is_owner`` from the same
coercion of the authoritative ``srv_servers.owner_id``. That single in-memory
comparison is the whole hierarchy gate -- there is no SQL backstop.

A malformed-but-coercible owner value -- ``"05"``, a padded string, ``5.9`` or
a bool -- silently becomes a real user id, so a non-owner moderator is treated
as the server owner and ``can_manage_member`` grants authority over *every*
member (including the real owner). Only a genuine integer id from the
authoritative owner row may satisfy either check.
"""

import pytest

from src.core.servers.manager import server_owner_cache
from src.core.voice.exceptions import PermissionDeniedError
from src.core.voice.manager.moderation import ModerationMixin

pytestmark = pytest.mark.unit

_SERVER_ID = 777
_PERMISSION = "voice.mute_members"
_TARGET_ID = 8  # deliberately distinct from any coerced moderator id


class _DB:
    """Minimal DB: hand back the crafted owner row on the srv_servers probe."""

    def __init__(self, owner_value):
        self.owner_value = owner_value

    def fetch_one(self, query, params=None):
        if "srv_servers" in query:
            return {"owner_id": self.owner_value}
        return None


class _Servers:
    """No roles for anyone: a genuine non-owner cannot outrank by hierarchy."""

    def _get_member_role_rows(self, server_id, user_id):
        return []


class _Service(ModerationMixin):
    def __init__(self, owner_value):
        self._db = _DB(owner_value)
        self._servers = _Servers()


def _force_authoritative_row(monkeypatch):
    """Always take the DB-row branch and never launder the value via the cache."""
    monkeypatch.setattr(
        server_owner_cache,
        "get_cached_server_owner",
        lambda db, sid: server_owner_cache._MISS,
    )
    monkeypatch.setattr(server_owner_cache, "prime_server_owner", lambda db, sid, oid: None)


def _gate_allows(monkeypatch, owner_value, moderator_id, target_id=_TARGET_ID):
    try:
        _Service(owner_value)._require_can_moderate_member(
            moderator_id, target_id, _SERVER_ID, _PERMISSION
        )
        return True
    except PermissionDeniedError:
        return False


@pytest.mark.parametrize(
    "owner_value, coerced_mod_id",
    [
        ("05", 5),  # leading-zero string: int() -> 5
        (" 5 ", 5),  # padded string
        (5.9, 5),  # float truncates to 5
        ("5", 5),  # plain numeric string
        (True, 1),  # bool coerces to 1
    ],
)
def test_malformed_owner_row_does_not_grant_moderation_authority(
    monkeypatch, owner_value, coerced_mod_id
):
    _force_authoritative_row(monkeypatch)
    # Pre-fix: int(owner_value) == coerced_mod_id -> is_owner=True ->
    # can_manage_member returns True for a member the moderator cannot outrank.
    assert _gate_allows(monkeypatch, owner_value, coerced_mod_id) is False, (
        f"malformed owner_id {owner_value!r} was coerced into owner-level "
        f"moderation authority for user {coerced_mod_id}"
    )


def test_genuine_integer_owner_row_still_grants(monkeypatch):
    """Non-vacuous: the real owner still passes the hierarchy gate."""
    _force_authoritative_row(monkeypatch)
    assert _gate_allows(monkeypatch, 5, 5) is True


def test_non_owner_integer_row_fails_closed(monkeypatch):
    _force_authoritative_row(monkeypatch)
    assert _gate_allows(monkeypatch, 7, 5) is False


def test_malformed_owner_is_not_laundered_into_owner_cache():
    """A coerced value must never be cached as a genuine owner id.

    ``prime_server_owner`` stores ``int(owner_id)``. If a malformed row were
    primed, a subsequent cache hit would hand back a genuine ``int`` and pass
    the type guard, re-opening the grant on the second call.
    """
    # Use the real cache functions; just isolate this test from prior state.
    server_owner_cache.invalidate_server_owner_cache()
    service = _Service("05")

    with pytest.raises(PermissionDeniedError):
        service._require_can_moderate_member(5, _TARGET_ID, _SERVER_ID, _PERMISSION)
    # Second call would hit the cache if the first had primed a coerced int.
    with pytest.raises(PermissionDeniedError):
        service._require_can_moderate_member(5, _TARGET_ID, _SERVER_ID, _PERMISSION)
