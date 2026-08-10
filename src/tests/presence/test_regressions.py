"""
Regression tests for the presence-system audit fixes (P1-P16).

The Valkey-backed paths are exercised against the in-memory ``FakeGlideClient``
(see ``src/tests/test_valkey.py``) so they run without a live server, exactly
like the rest of the cache test suite.
"""

import asyncio
from unittest.mock import Mock

import pytest

from src.tests.test_valkey import FakeGlideClient


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_valkey():
    """Install a FakeGlideClient-backed Valkey client as the module default.

    The global default client is replaced for the duration of the test and
    restored afterwards. The key prefix is stripped so assertions can use the
    exact presence keys.
    """
    from src.core.database import valkey_client
    from src.core.database.valkey_client import ValkeyClient

    saved_default = valkey_client._default_client

    client = ValkeyClient()
    client.key_prefix = ""
    client.enabled = True
    fake = FakeGlideClient()
    client._client = fake
    client._connected = True
    valkey_client._default_client = client

    try:
        yield fake, client
    finally:
        valkey_client._default_client = saved_default
        try:
            client.close()
        except Exception:
            pass


@pytest.fixture
def valkey_presence(db, rel_manager, server_manager, fake_valkey):
    """PresenceManager wired for cache-backed (Valkey) behaviour."""
    from src.core.presence.manager import PresenceManager

    return PresenceManager(
        db,
        relationships_module=rel_manager,
        servers_module=server_manager,
    )


def _make_connection(user_id):
    """Build an authenticated Connection with a mock websocket."""
    from src.api.websocket.connection import Connection, ConnectionState

    conn = Connection(websocket=Mock(), connection_id=f"conn-{user_id}")
    conn.user_id = user_id
    conn.state = ConnectionState.READY
    return conn


def _typing_row_count(db, user_id, channel_id):
    return db.fetch_one(
        "SELECT COUNT(*) AS n FROM pres_typing WHERE user_id = ? AND channel_id = ?",
        (user_id, channel_id),
    )["n"]


# ---------------------------------------------------------------------------
# P1: cache must be re-seeded by mutating operations
# ---------------------------------------------------------------------------


class TestCustomStatusCacheRefresh:
    def test_set_custom_status_visible_through_warm_cache(
        self, user_pool, valkey_presence
    ):
        user = user_pool.get_user()

        result = valkey_presence.set_custom_status(
            user.id, "Playing games", emoji="game"
        )
        assert result.custom_status is not None
        assert result.custom_status.text == "Playing games"

        warm = valkey_presence.get_presence(user.id, use_cache=True)
        assert warm.custom_status is not None
        assert warm.custom_status.text == "Playing games"
        assert warm.custom_status.emoji == "game"

    def test_clear_custom_status_visible_through_warm_cache(
        self, user_pool, valkey_presence
    ):
        user = user_pool.get_user()
        valkey_presence.set_custom_status(user.id, "Temporary")

        cleared = valkey_presence.clear_custom_status(user.id)
        assert cleared.custom_status is None

        warm = valkey_presence.get_presence(user.id, use_cache=True)
        assert warm.custom_status is None


class TestUpdateLastSeenCacheRefresh:
    def test_update_last_seen_refreshes_cache(self, user_pool, valkey_presence):
        user = user_pool.get_user()

        before = valkey_presence.set_status(user.id, "online")
        updated = valkey_presence.update_last_seen(user.id)

        assert updated.last_seen >= before.last_seen
        # The warm read must reflect the freshly written value, not a stale
        # cache entry written by the earlier set_status.
        warm = valkey_presence.get_presence(user.id, use_cache=True)
        assert warm.last_seen == updated.last_seen


# ---------------------------------------------------------------------------
# P2: cache_presence must include user_id and never crash hydration
# ---------------------------------------------------------------------------


class TestCacheSchemaFix:
    def test_cache_presence_entry_includes_user_id(self, user_pool, fake_valkey):
        from src.core.database.cache import cache_presence, get_cached_presence

        user = user_pool.get_user()
        assert cache_presence(user.id, "online", "Busy") is True

        entry = get_cached_presence(user.id)
        assert entry is not None
        assert entry["user_id"] == user.id

    def test_minimal_cache_entry_does_not_crash_get_presence(
        self, user_pool, valkey_presence, fake_valkey
    ):
        from src.core.database.cache import cache_presence, get_cached_presence

        user = user_pool.get_user()
        # The standalone helper writes a minimal schema (custom_status is a
        # plain string). get_presence must purge it and fall back to the DB
        # instead of crashing on hydration.
        assert cache_presence(user.id, "online", "Playing games") is True

        presence = valkey_presence.get_presence(user.id, use_cache=True)
        assert presence is not None
        assert presence.user_id == user.id

        # The entry was purged and rebuilt with the full schema.
        rebuilt = get_cached_presence(user.id)
        assert rebuilt is not None
        assert rebuilt["user_id"] == user.id
        assert rebuilt["custom_status"] is None


# ---------------------------------------------------------------------------
# P10: custom_status.created_at survives the cache round trip
# ---------------------------------------------------------------------------


class TestCustomStatusCreatedAtRoundTrip:
    def test_created_at_preserved_in_cache_round_trip(self, user_pool, valkey_presence):
        user = user_pool.get_user()
        result = valkey_presence.set_custom_status(user.id, "Working", emoji="work")
        assert result.custom_status is not None
        original_created_at = result.custom_status.created_at
        assert original_created_at > 0

        encoded = valkey_presence._presence_to_dict(result)
        assert encoded["custom_status"]["created_at"] == original_created_at

        decoded = valkey_presence._dict_to_presence(encoded)
        assert decoded.custom_status is not None
        assert decoded.custom_status.created_at == original_created_at

        warm = valkey_presence.get_presence(user.id, use_cache=True)
        assert warm.custom_status is not None
        assert warm.custom_status.created_at == original_created_at


# ---------------------------------------------------------------------------
# P11/P16: offline and bulk presence must be cached
# ---------------------------------------------------------------------------


class TestOfflineAndBulkCaching:
    def test_offline_presence_is_cached(self, user_pool, valkey_presence, fake_valkey):
        from src.core.database.cache import get_cached_presence

        user = user_pool.get_user()
        # Never-online user: no pres_presence row exists yet.
        presence = valkey_presence.get_presence(user.id, use_cache=True)
        assert presence.status.value == "offline"
        assert presence.last_seen == 0

        entry = get_cached_presence(user.id)
        assert entry is not None
        assert entry["status"] == "offline"
        assert entry["last_seen"] == 0

    def test_bulk_presence_caches_rows(self, user_pool, valkey_presence, fake_valkey):
        from src.core.database.cache import get_cached_presence

        user = user_pool.get_user()
        results = valkey_presence.get_presences([user.id])
        assert results[0].user_id == user.id
        assert results[0].status.value == "offline"

        entry = get_cached_presence(user.id)
        assert entry is not None
        assert entry["status"] == "offline"

    def test_bulk_presence_includes_focus(
        self, user_pool, valkey_presence, fake_valkey
    ):
        from src.core.database.cache import get_cached_presence

        user = user_pool.get_user()
        assert (
            valkey_presence.set_focused_channel(user.id, channel_id=42, server_id=7)
            is True
        )

        results = valkey_presence.get_presences([user.id])
        assert results[0].current_channel_id == 42
        assert results[0].current_server_id == 7

        # The cached dict is schema-identical to the single-user path, so the
        # focused state is not dropped.
        entry = get_cached_presence(user.id)
        assert entry is not None
        assert entry["current_channel_id"] == 42
        assert entry["current_server_id"] == 7

        warm = valkey_presence.get_presence(user.id, use_cache=True)
        assert warm.current_channel_id == 42


# ---------------------------------------------------------------------------
# P5: online-users set TTL + focus applied to single get_presence
# ---------------------------------------------------------------------------


class TestOnlineSetAndFocus:
    def test_online_users_set_gets_ttl(self, user_pool, valkey_presence, fake_valkey):
        fake, client = fake_valkey
        user = user_pool.get_user()
        valkey_presence.set_status(user.id, "online")

        # The underlying fake keeps a per-key expiry map; the online-users set
        # must carry a bounded TTL so it cannot grow unbounded.
        assert "presence:online_users" in fake._expiry
        assert str(user.id) in client.smembers("presence:online_users")

    def test_single_get_presence_applies_focus(
        self, user_pool, valkey_presence, fake_valkey
    ):
        user = user_pool.get_user()
        assert valkey_presence.set_focused_channel(user.id, channel_id=99) is True

        presence = valkey_presence.get_presence(user.id, use_cache=False)
        assert presence.current_channel_id == 99


# ---------------------------------------------------------------------------
# P9: invalid activity type must raise, not coerce to PLAYING
# ---------------------------------------------------------------------------


class TestInvalidActivity:
    def test_invalid_activity_type_raises(self, user_pool, valkey_presence):
        from src.core.presence.exceptions import InvalidActivityError

        user = user_pool.get_user()
        with pytest.raises(InvalidActivityError):
            valkey_presence.set_activity(user.id, "not_a_real_activity", "Whatever")


# ---------------------------------------------------------------------------
# P7: get_online_server_members membership gate
# ---------------------------------------------------------------------------


class TestOnlineServerMembersMembership:
    def _server_with_members(self, valkey_presence, server_manager, user_pool):
        owner = user_pool.get_user()
        member = user_pool.get_user()
        outsider = user_pool.get_user()

        server = server_manager.create_server(owner.id, "P7 Test Server")
        server_manager.add_member(server.id, member.id)

        # Bring owner + member online so the roster is non-empty.
        valkey_presence.set_status(owner.id, "online")
        valkey_presence.set_status(member.id, "online")
        return owner, member, outsider, server

    def test_member_can_see_online_members(
        self, user_pool, valkey_presence, server_manager
    ):
        owner, member, _outsider, server = self._server_with_members(
            valkey_presence, server_manager, user_pool
        )
        online = valkey_presence.get_online_server_members(owner.id, server.id)
        assert set(online) == {owner.id, member.id}

    def test_non_member_gets_empty_list(
        self, user_pool, valkey_presence, server_manager
    ):
        _owner, _member, outsider, server = self._server_with_members(
            valkey_presence, server_manager, user_pool
        )
        assert valkey_presence.get_online_server_members(outsider.id, server.id) == []

    def test_system_caller_bypasses_membership_check(
        self, user_pool, valkey_presence, server_manager
    ):
        owner, member, _outsider, server = self._server_with_members(
            valkey_presence, server_manager, user_pool
        )
        online = valkey_presence.get_online_server_members(0, server.id)
        assert set(online) == {owner.id, member.id}


# ---------------------------------------------------------------------------
# P12: can_see_presence handles str/int ids
# ---------------------------------------------------------------------------


class TestVisibilityIntCoercion:
    def test_can_see_presence_mixed_types(self, user_pool, valkey_presence):
        user = user_pool.get_user()
        assert valkey_presence.can_see_presence(str(user.id), user.id) is True
        assert valkey_presence.can_see_presence(user.id, str(user.id)) is True

        # Two distinct, unblocked users: viewer sees real presence.
        other = user_pool.get_user()
        assert valkey_presence.can_see_presence(str(user.id), other.id) is True

        # Invisible targets stay hidden regardless of id type.
        valkey_presence.set_status(other.id, "invisible")
        assert valkey_presence.can_see_presence(str(user.id), other.id) is False


# ---------------------------------------------------------------------------
# P8: get_typing_users returns real DB timestamps
# ---------------------------------------------------------------------------


class TestTypingTimestamps:
    def test_get_typing_users_returns_real_timestamps(
        self, user_pool, valkey_presence, fake_valkey
    ):
        user = user_pool.get_user()
        started = valkey_presence.start_typing(user.id, 555)

        indicators = valkey_presence.get_typing_users(555)
        assert len(indicators) == 1
        ind = indicators[0]
        # Exact match with the real DB row (the fabricated fallback would
        # differ by ~1000ms).
        assert ind.started_at == started.started_at
        assert ind.expires_at == started.expires_at
        assert ind.expires_at - ind.started_at == valkey_presence._typing_timeout_ms


# ---------------------------------------------------------------------------
# P4: offline presence only after the last connection drops
# ---------------------------------------------------------------------------


class TestOfflineDispatchConnectionCount:
    def test_stays_online_while_other_connections_exist(self):
        from src.api.websocket.gateway import _dispatch_offline_presence

        dispatcher = Mock()
        dispatcher.dispatch_event = Mock()
        session_manager = Mock()
        session_manager.get_user_connection_count.return_value = 2
        presence_module = Mock()
        presence_module.clear_all_typing.return_value = []

        asyncio.run(
            _dispatch_offline_presence(
                123,
                presence_module,
                dispatcher,
                session_manager=session_manager,
            )
        )

        dispatcher.dispatch_event.assert_not_called()
        presence_module.set_status.assert_not_called()
        presence_module.clear_all_typing.assert_not_called()

    def test_last_connection_proceeds_without_error(self):
        from src.api.websocket.gateway import _dispatch_offline_presence

        dispatcher = Mock()
        dispatcher.dispatch_event = Mock()
        session_manager = Mock()
        session_manager.get_user_connection_count.return_value = 1
        presence_module = Mock()
        presence_module.clear_all_typing.return_value = []

        # With no friends/shared members (no api setup) this should simply
        # fall through without dispatching anything.
        asyncio.run(
            _dispatch_offline_presence(
                123,
                presence_module,
                dispatcher,
                session_manager=session_manager,
            )
        )
        dispatcher.dispatch_event.assert_not_called()


# ---------------------------------------------------------------------------
# P15: ws presence handler rejects invalid status/activity
# ---------------------------------------------------------------------------


class TestWsPresenceValidation:
    def _handler(self, presence_module):
        from src.api.websocket.handlers.presence import PresenceHandler

        return PresenceHandler(presence_module=presence_module)

    def test_invalid_status_returns_decode_error(self, valkey_presence):
        from src.api.websocket.opcodes import GatewayCloseCode

        conn = _make_connection(1)
        op, data, close = asyncio.run(
            self._handler(valkey_presence).handle_presence_update(
                conn, {"status": "not_a_status"}
            )
        )
        assert (op, data) == (None, None)
        assert close == int(GatewayCloseCode.DECODE_ERROR)

    def test_invalid_activity_returns_decode_error(self, valkey_presence):
        from src.api.websocket.opcodes import GatewayCloseCode

        conn = _make_connection(1)
        op, data, close = asyncio.run(
            self._handler(valkey_presence).handle_presence_update(
                conn, {"status": "online", "activities": [{"type": "bogus"}]}
            )
        )
        assert (op, data) == (None, None)
        assert close == int(GatewayCloseCode.DECODE_ERROR)

    def test_valid_update_does_not_raise(self, user_pool, valkey_presence):
        user = user_pool.get_user()
        conn = _make_connection(user.id)
        op, data, close = asyncio.run(
            self._handler(valkey_presence).handle_presence_update(
                conn, {"status": "online"}
            )
        )
        assert (op, data, close) == (None, None, None)


# ---------------------------------------------------------------------------
# P14: never-online users report last_seen = None
# ---------------------------------------------------------------------------


class TestPresenceResponseLastSeen:
    def test_never_online_reports_none(self, user_pool, valkey_presence):
        from src.api.routes.presence import _presence_to_response

        user = user_pool.get_user()
        presence = valkey_presence.get_presence(user.id, use_cache=False)
        assert presence.last_seen == 0

        response = _presence_to_response(presence, user.id)
        assert response.last_seen is None

    def test_online_reports_timestamp(self, user_pool, valkey_presence):
        from src.api.routes.presence import _presence_to_response

        user = user_pool.get_user()
        presence = valkey_presence.set_status(user.id, "online")
        assert presence.last_seen > 0

        response = _presence_to_response(presence, user.id)
        assert response.last_seen == presence.last_seen


# ---------------------------------------------------------------------------
# P3: presence targets exclude blocked users; invisible data scrubbed
# ---------------------------------------------------------------------------


class TestPresenceTargets:
    def test_blocked_users_excluded_from_targets(
        self,
        monkeypatch,
        user_pool,
        rel_manager,
        server_manager,
    ):
        import src.api as api_mod

        monkeypatch.setattr(api_mod, "_relationships", rel_manager)
        monkeypatch.setattr(api_mod, "_servers", server_manager)

        from src.api.routes.presence import _get_presence_targets

        blocker = user_pool.get_user()
        blocked = user_pool.get_user()

        server = server_manager.create_server(blocker.id, "P3 Test Server")
        server_manager.add_member(server.id, blocked.id)

        # Shared-server membership alone puts the blocked user in the set.
        targets = _get_presence_targets(blocker.id)
        assert blocked.id in targets

        rel_manager.block_user(blocked.id, blocker.id)
        # _get_presence_targets is @cached; drop the entry built before the
        # block so the second call recomputes with the block filter applied.
        _get_presence_targets.invalidate(blocker.id)
        targets = _get_presence_targets(blocker.id)
        assert blocked.id not in targets

    def test_invisible_event_data_is_scrubbed(self):
        from src.api.routes.presence import _build_presence_event_data

        data = _build_presence_event_data(
            1, "invisible", "secret status", "secret emoji"
        )
        assert data == {
            "user_id": "1",
            "status": "offline",
            "custom_status": None,
            "custom_emoji": None,
        }

        visible = _build_presence_event_data(1, "online", "hello", "x")
        assert visible["status"] == "online"
        assert visible["custom_status"] == "hello"
        assert visible["custom_emoji"] == "x"


# ---------------------------------------------------------------------------
# P5: heartbeat keeps last_seen fresh
# ---------------------------------------------------------------------------


class TestHeartbeatLastSeen:
    def test_heartbeat_updates_last_seen(self, user_pool):
        from src.api.websocket.handlers.connection import ConnectionHandler

        user = user_pool.get_user()
        conn = _make_connection(user.id)

        presence_module = Mock()
        presence_module.update_last_seen = Mock(return_value=None)

        handler = ConnectionHandler(
            session_manager=Mock(), presence_module=presence_module
        )
        asyncio.run(handler.handle_heartbeat(conn, None))

        presence_module.update_last_seen.assert_called_once_with(user.id)

    def test_heartbeat_skips_update_when_unauthenticated(self):
        from src.api.websocket.handlers.connection import ConnectionHandler
        from src.api.websocket.connection import Connection, ConnectionState

        conn = Connection(websocket=Mock(), connection_id="anon")
        conn.state = ConnectionState.CONNECTED

        presence_module = Mock()
        presence_module.update_last_seen = Mock(return_value=None)

        handler = ConnectionHandler(
            session_manager=Mock(), presence_module=presence_module
        )
        asyncio.run(handler.handle_heartbeat(conn, None))

        presence_module.update_last_seen.assert_not_called()


# ---------------------------------------------------------------------------
# P6/P13: typing events are scoped to channel members, never recorded for
# inaccessible channels
# ---------------------------------------------------------------------------


class TestTypingChannelScoping:
    def test_get_channel_recipients_exclude_non_members(
        self,
        monkeypatch,
        user_pool,
        rel_manager,
        server_manager,
    ):
        import src.api as api_mod

        monkeypatch.setattr(api_mod, "_servers", server_manager)
        monkeypatch.setattr(api_mod, "_relationships", rel_manager)

        from src.api.websocket.handlers.presence import _get_channel_recipients

        owner = user_pool.get_user()
        member = user_pool.get_user()
        outsider = user_pool.get_user()

        server = server_manager.create_server(owner.id, "P6 Test Server")
        server_manager.add_member(server.id, member.id)
        channel = server_manager.create_channel(owner.id, server.id, "general")

        recipients, server_id = _get_channel_recipients(owner.id, channel.id)
        assert server_id == server.id
        assert member.id in recipients
        assert owner.id not in recipients
        assert outsider.id not in recipients

    def test_typing_start_inaccessible_channel_not_recorded(
        self, monkeypatch, user_pool, valkey_presence
    ):
        import src.api as api_mod

        monkeypatch.setattr(api_mod, "get_servers", lambda: None)
        monkeypatch.setattr(api_mod, "get_messaging", lambda: None)

        from src.api.websocket.handlers.presence import PresenceHandler

        user = user_pool.get_user()
        conn = _make_connection(user.id)

        handler = PresenceHandler(presence_module=valkey_presence)
        asyncio.run(handler.handle_typing_start(conn, {"channel_id": "999999"}))
        assert _typing_row_count(valkey_presence.db, user.id, 999999) == 0

    def test_typing_stop_dispatches_only_to_channel_members(
        self,
        monkeypatch,
        user_pool,
        rel_manager,
        server_manager,
        presence_manager,
    ):
        import src.api as api_mod
        import src.api.websocket as ws_mod

        monkeypatch.setattr(api_mod, "_servers", server_manager)
        monkeypatch.setattr(api_mod, "_relationships", rel_manager)

        from src.api.websocket.handlers.presence import PresenceHandler

        owner = user_pool.get_user()
        member = user_pool.get_user()
        # A friend who is NOT in the server must never receive typing events.
        friend_outsider = user_pool.get_user()
        request = rel_manager.send_friend_request(owner.id, friend_outsider.id)
        rel_manager.accept_friend_request(friend_outsider.id, request.id)

        server = server_manager.create_server(owner.id, "P6b Test Server")
        server_manager.add_member(server.id, member.id)
        channel = server_manager.create_channel(owner.id, server.id, "general")

        dispatched = []
        dispatcher = Mock()

        async def fake_dispatch(event, user_ids):
            dispatched.append((event.event_type, event.data, list(user_ids)))

        dispatcher.dispatch_event = fake_dispatch
        monkeypatch.setattr(ws_mod, "is_setup", lambda: True)
        monkeypatch.setattr(ws_mod, "get_dispatcher", lambda: dispatcher)

        conn = _make_connection(owner.id)
        handler = PresenceHandler(presence_module=presence_manager)

        asyncio.run(handler.handle_typing_start(conn, {"channel_id": str(channel.id)}))
        asyncio.run(handler.handle_typing_stop(conn, {"channel_id": str(channel.id)}))

        stop_events = [
            (t, d, ids)
            for t, d, ids in dispatched
            if d and d.get("channel_id") == str(channel.id)
        ]
        assert stop_events, "expected at least one typing event for the channel"
        for _t, _d, ids in stop_events:
            assert member.id in ids
            assert friend_outsider.id not in ids
            assert owner.id not in ids

    def test_typing_route_ignores_unknown_channel(
        self,
        monkeypatch,
        user_pool,
        presence_manager,
        server_manager,
    ):
        import src.api as api_mod

        monkeypatch.setattr(api_mod, "_servers", server_manager)
        monkeypatch.setattr(api_mod, "_presence", presence_manager)

        from src.api.routes.messages.typing import trigger_typing
        from src.core.auth.models import AccountType, TokenInfo

        user = user_pool.get_user()
        token = TokenInfo(
            valid=True,
            token_type="user",
            account_id=user.id,
            user_id=user.id,
            session_id=123,
            permissions={},
            rate_limit_tier="standard",
            expires_at=None,
            username=user.username,
            account_type=AccountType.USER,
        )

        result = asyncio.run(trigger_typing("999999", token))
        assert result.success is True
        assert _typing_row_count(presence_manager.db, user.id, 999999) == 0
