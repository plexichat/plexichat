"""Channel-list core read: the membership gate must run before the cache.

``ChannelHandler.get_channels`` memoizes the permission-filtered channel list
under ``server_channels:*``. Its membership authorization check lived *inside*
the cached loader, so on a warm hit the function body (and therefore the gate)
never ran: a member who was removed from the server (kick/ban/leave, including
core-only automod/admin/bot callers) kept receiving the pre-revocation channel
list straight from the cache until a TTL expired or an eviction happened to
fire. Eviction-on-revocation is a mitigation, not an authorization boundary;
the gate itself must be evaluated on every read, before any cache tier.

Two behavioral contracts are pinned here with the REAL @cached decorator
running against its in-memory tier:

1. After membership is revoked directly in the store (no eviction helper), the
   next read is denied rather than served warm. A DB mutation made after priming
   proves the cache was actually hot when the gate is bypassed.
2. The gate call is re-issued on every read while the expensive channel scan is
   memoized (fail closed, no reliance on eviction).
"""

from __future__ import annotations

import pytest

from src.core.database.cache import decorators as cache_decorators
from src.core.database.cache import operations as cache_operations
from src.core.database.cache.base import mem_cache_clear_pattern
from src.core.servers.exceptions import ServerAccessDeniedError
from src.core.servers.handlers.channel_handler import ChannelHandler

SERVER_ID = 5
VIEWER = 101


def _channel_row(cid):
    return {
        "id": cid,
        "server_id": SERVER_ID,
        "name": f"channel-{cid}",
        "channel_type": "text",
        "category_id": None,
        "position": cid,
        "topic_encrypted": None,
        "nsfw": 0,
        "created_at": 1,
        "updated_at": 1,
    }


class _Db:
    """Minimal DB stub: only the channel scan is issued by the cached loader."""

    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.channel_queries = 0

    def fetch_all(self, sql, params):
        if "srv_channels" in sql:
            self.channel_queries += 1
            return [dict(r) for r in self.rows]
        return []


class _Perms:
    def get_permissions_batch(self, user_id, server_id, channel_ids):
        return {cid: {"channels.view": True} for cid in channel_ids}


class _Manager:
    def __init__(self, db, members):
        self._db = db
        self._members = set(members)
        self._encrypt_descriptions = False
        self.role_handler = _Perms()
        self.gate_calls = 0

    def _is_member(self, server_id, user_id):
        self.gate_calls += 1
        return user_id in self._members


@pytest.fixture
def cached_env(monkeypatch):
    """Run the real @cached loader against the in-memory tier only."""
    monkeypatch.setattr(cache_decorators, "get_client", lambda: None)
    monkeypatch.setattr(cache_operations, "get_client", lambda: None)
    mem_cache_clear_pattern("server_channels:*")
    yield
    mem_cache_clear_pattern("server_channels:*")


def test_gate_reruns_on_warm_hit_after_revocation(cached_env):
    db = _Db([_channel_row(1), _channel_row(2)])
    mgr = _Manager(db, members={VIEWER})
    handler = ChannelHandler(mgr)

    warm = handler.get_channels(VIEWER, SERVER_ID)
    assert [c.id for c in warm] == [1, 2]

    # Prove the cache is genuinely hot: a DB change is invisible on the hit.
    db.rows.append(_channel_row(3))
    assert [c.id for c in handler.get_channels(VIEWER, SERVER_ID)] == [1, 2]

    # Access is revoked directly in the membership store. No eviction helper is
    # invoked -- a live authorization boundary must not depend on one. With the
    # gate inside the cached loader this warm read silently succeeds.
    mgr._members.discard(VIEWER)
    with pytest.raises(ServerAccessDeniedError):
        handler.get_channels(VIEWER, SERVER_ID)


def test_gate_called_on_every_read_while_scan_stays_cached(cached_env):
    db = _Db([_channel_row(1)])
    mgr = _Manager(db, members={VIEWER})
    handler = ChannelHandler(mgr)

    handler.get_channels(VIEWER, SERVER_ID)
    assert mgr.gate_calls == 1
    assert db.channel_queries == 1

    handler.get_channels(VIEWER, SERVER_ID)
    # The membership gate re-runs on every call; the expensive channel scan does
    # not (it is memoized). Before the fix this was gate_calls == 1.
    assert mgr.gate_calls == 2
    assert db.channel_queries == 1
