"""Typing/presence viewer audiences must not admit malformed member ids.

``_get_channel_recipients`` builds the server-channel viewer audience from the
raw ``srv_members.user_id`` projection returned by ``get_member_user_ids``
(``[row["user_id"] for row in rows]``, no type filtering). The authoritative
``has_permission`` gate itself coerces with ``int(user_id)`` (permissions.py),
so a malformed-but-coercible id (``"05"``, ``" 8 "``, ``True``, ``5.9``) can pass
the ``channels.view`` check. The raw value is then appended to ``allowed`` and
laundered into a real principal either by ``TypingAudienceCache.put`` (warm
path, ``int(uid)``) or by ``GatewayDispatcher.dispatch_event`` (cold path,
``int(uid)``). A row that never proved that user's view right therefore admits
the wrong principal to TYPING_START/STOP recipients. Only genuine integer ids
from the member roster may enter the viewer set.
"""

import pytest

import src.api as api
from src.api.websocket.handlers import presence as hp


class _Channel:
    def __init__(self, server_id: int) -> None:
        self.server_id = server_id


class _Servers:
    """Stubs the authoritative member roster with malformed coercible ids."""

    def __init__(self, members) -> None:
        self._members = list(members)

    def get_channel(self, channel_id, user_id):
        return _Channel(100)

    def get_member_user_ids(self, server_id):
        return list(self._members)

    def has_permission(self, user_id, server_id, perm, channel_id):
        # Real has_permission coerces int(user_id), so coercible junk passes.
        return True


@pytest.fixture(autouse=True)
def _clear_audience_cache():
    hp._typing_audience_cache.clear()
    yield
    hp._typing_audience_cache.clear()


def _wire(monkeypatch, servers) -> None:
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_relationships", lambda: None)


# Raw srv_members.user_id projection: genuine 7 and 42 plus coercible junk
# that int() would launder into 5, 8, 1 and 5.
_RAW_MEMBERS = ["05", 7, " 8 ", True, 5.9, 42]


@pytest.mark.websocket
def test_typing_audience_rejects_malformed_member_ids(monkeypatch):
    _wire(monkeypatch, _Servers(_RAW_MEMBERS))

    recipients, server_id = hp._get_channel_recipients(999, 500)

    assert server_id == 100
    assert recipients == [7, 42]
    for wrong in (5, 8, 1):
        assert wrong not in recipients, f"laundered viewer id {wrong} admitted"


@pytest.mark.websocket
def test_typing_viewer_cache_rejects_malformed_ids(monkeypatch):
    _wire(monkeypatch, _Servers(_RAW_MEMBERS))

    hp._get_channel_recipients(999, 500)  # cold path populates the cache
    cached = hp._typing_audience_cache.get(100, 500)

    assert cached == [7, 42]
