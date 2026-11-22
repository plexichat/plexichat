"""Server-channel typing frames must not reach users who block the sender.

Typing events are dispatched with an *explicit* recipient list, so the gateway
router's DM block filter (implicit routing only) never sees them. The server
channel audience resolver derives viewers from ``channels.view`` permission
alone; a blocked pair can still share a server. Without an explicit two-way
block gate a blocked user keeps receiving the other's TYPING_START/STOP frames.
The filter is applied per actor at read time so the cached viewer set stays
sender-agnostic.
"""

from __future__ import annotations

import pytest

import src.api as api
from src.api.websocket.handlers import presence as hp


class _Channel:
    def __init__(self, server_id: int) -> None:
        self.server_id = server_id


class _Servers:
    def __init__(self, sid: int, members: list[int]) -> None:
        self._sid = sid
        self._members = list(members)

    def get_channel(self, channel_id: int, user_id: int):
        if self._sid is None:
            return None
        return _Channel(self._sid)

    def get_member_user_ids(self, server_id: int) -> list[int]:
        return list(self._members)

    def has_permission(self, user_id, server_id, perm, channel_id) -> bool:
        return True


class _Relationships:
    def __init__(self, blocked_by_user=None, explode_for: int | None = None) -> None:
        self._blocked = {int(k): set(v) for k, v in (blocked_by_user or {}).items()}
        self._explode_for = explode_for
        self.lookups: list[int] = []

    def get_all_blocked_ids(self, user_id) -> list[int]:
        uid = int(user_id)
        self.lookups.append(uid)
        if uid == self._explode_for:
            raise RuntimeError("relationship store unavailable")
        return list(self._blocked.get(uid, set()))


@pytest.fixture(autouse=True)
def _clear_audience_cache():
    hp._typing_audience_cache.clear()
    yield
    hp._typing_audience_cache.clear()


def _wire(monkeypatch, servers, relationships) -> None:
    import src.core.relationships as rel_module

    monkeypatch.setattr(rel_module, "_setup_complete", True, raising=False)
    monkeypatch.setattr(rel_module, "_manager", object(), raising=False)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_relationships", lambda: relationships)


@pytest.mark.websocket
def test_cold_channel_typing_audience_drops_blocked_user(monkeypatch) -> None:
    rel = _Relationships(blocked_by_user={1: [3]})
    _wire(monkeypatch, _Servers(100, [1, 2, 3, 4]), rel)

    recipients, server_id = hp._get_channel_recipients(1, 500)

    assert server_id == 100
    assert set(recipients) == {2, 4}, "blocked user 3 must not receive typing"
    assert rel.lookups == [1]


@pytest.mark.websocket
def test_channel_typing_fails_closed_on_block_lookup_error(monkeypatch) -> None:
    rel = _Relationships(explode_for=1)
    _wire(monkeypatch, _Servers(100, [1, 2, 3]), rel)

    recipients, _ = hp._get_channel_recipients(1, 500)

    assert recipients == []


@pytest.mark.websocket
def test_block_filter_is_per_actor_on_warm_cache(monkeypatch) -> None:
    rel = _Relationships(blocked_by_user={1: [2]})
    _wire(monkeypatch, _Servers(100, [1, 2, 3]), rel)

    first, _ = hp._get_channel_recipients(1, 500)
    assert set(first) == {3}

    rel._blocked[3] = {2}
    second, _ = hp._get_channel_recipients(3, 500)
    assert set(second) == {1}
    assert hp._typing_audience_cache.stats()["hits"] >= 1
