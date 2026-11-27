"""Cache-coherency regressions for the per-viewer member roster list.

``GET /servers/{id}/members`` memoizes a page of the roster for a short TTL.
That page embeds each member's nickname, role ids, timeout state and presence,
so every write that can change those fields must evict *all* viewers' cached
pages. Before this fix the core member-update and member-role handlers cleared
only ``member_data:*`` / permission caches, and the membership-lifecycle helper
cleared neither the API roster tier -- leaving a stale 200 (old nickname, old
roles, a removed member still listed) for the full 30s TTL.

These tests pin: (1) warm reads are byte-for-byte identical and hit no backend,
(2) credentials never share a cache entry (including bot vs owner), and
(3) every roster-changing mutation reaches the ``server_members_api:*`` glob.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.database import invalidate_pattern
from src.core.database.cache.base import mem_cache_matching_keys


class TokenInfo:
    """Stand-in with the real class name used by cache-key generation."""

    def __init__(self, user_id, token_type="user", account_id=None):
        self.user_id = user_id
        self.token_type = token_type
        self.account_id = account_id


def _member(user_id, nickname=None, roles=None):
    return {
        "id": 100 + user_id,
        "user_id": user_id,
        "nickname": nickname,
        "joined_at": 1,
        "roles": roles or [],
        "timeout_until": None,
        "timeout_reason": None,
    }


class _FakeServers:
    def __init__(self, rows):
        self.rows = list(rows)
        self.list_calls = 0

    def get_members(self, viewer_id, sid, limit, after):
        self.list_calls += 1
        return list(self.rows)


def _wire(monkeypatch, servers):
    from src.api.routes.servers import members

    monkeypatch.setattr(members.api, "get_servers", lambda: servers)
    monkeypatch.setattr(members.api, "get_auth", lambda: None)
    monkeypatch.setattr(members.api, "get_presence", lambda: None)
    return members


def _items(payload):
    """A cache hit reconstructs to a dict; a miss returns the pydantic model."""
    return payload["items"] if isinstance(payload, dict) else payload.items


def _field(item, name):
    """A warm item is a plain dict; a cold item is the pydantic response."""
    return item.get(name) if isinstance(item, dict) else getattr(item, name)


def _clean():
    invalidate_pattern("server_members_api:*")
    assert mem_cache_matching_keys("server_members_api:*") == []


class TestMemberRosterCacheKeys:
    def test_keys_partition_by_credential_and_bot_owner(self):
        from src.api.routes.servers import members

        builder = members.authenticated_cache_key(members._MEMBERS_LIST_CACHE_PREFIX)
        kwargs = {"server_id": "1", "limit": 100, "after": None}
        user7 = builder(current_user=TokenInfo(7), **kwargs)
        user8 = builder(current_user=TokenInfo(8), **kwargs)
        bot_of_7 = builder(
            current_user=TokenInfo(7, token_type="bot", account_id=99), **kwargs
        )

        assert len({user7, user8, bot_of_7}) == 3
        assert user7.startswith(members._MEMBERS_LIST_CACHE_PREFIX)
        assert ":bot:99:" in bot_of_7


class TestMemberRosterCacheCoherency:
    def test_warm_reads_are_identical_and_hit_no_backend(self, monkeypatch):
        _clean()
        servers = _FakeServers([_member(7, nickname="before")])
        members = _wire(monkeypatch, servers)
        token = TokenInfo(7)

        first = members.get_server_members(
            server_id="1", limit=100, after=None, current_user=token
        )
        servers.rows = [_member(7, nickname="after")]  # change behind the cache
        cached = members.get_server_members(
            server_id="1", limit=100, after=None, current_user=token
        )

        assert _field(_items(first)[0], "nickname") == "before"
        assert _field(_items(cached)[0], "nickname") == "before"
        assert servers.list_calls == 1  # warm read cost zero backend queries

        members._invalidate_member_list_cache()
        fresh = members.get_server_members(
            server_id="1", limit=100, after=None, current_user=token
        )
        assert _field(_items(fresh)[0], "nickname") == "after"
        assert servers.list_calls == 2

    def test_no_cross_credential_cache_reuse(self, monkeypatch):
        _clean()
        servers = _FakeServers([_member(7)])
        members = _wire(monkeypatch, servers)

        for token in (
            TokenInfo(7),
            TokenInfo(7),  # repeat -> hit, no extra backend call
            TokenInfo(8),  # another user -> its own entry
            TokenInfo(7, token_type="bot", account_id=99),  # bot namespace
        ):
            members.get_server_members(
                server_id="1", limit=100, after=None, current_user=token
            )

        assert servers.list_calls == 3  # four calls, three distinct credentials


class TestRosterInvalidationOnEveryMutation:
    def test_member_update_invalidates_roster(self):
        from src.core.servers.handlers.members import crud as crud_mod

        handler = object.__new__(crud_mod.MemberCrudMixin)
        handler.db = SimpleNamespace(execute=lambda *a, **k: None)
        handler.manager = MagicMock()
        handler.manager._get_timestamp.return_value = 1
        member = SimpleNamespace(
            id=10,
            user_id=7,
            nickname="old",
            muted=False,
            deafened=False,
            timeout_until=None,
            timeout_reason=None,
            roles=[],
        )
        handler.get_member = lambda sid, uid: member

        with patch.object(crud_mod, "invalidate_pattern") as invalidate:
            handler.update_member(7, 1, 7, nickname="new")

        patterns = {c.args[0] for c in invalidate.call_args_list}
        assert "server_members_api:*" in patterns

    @pytest.mark.parametrize(
        "route_name", ["assign_role_to_member", "remove_role_from_member"]
    )
    def test_member_role_change_invalidates_roster(self, route_name):
        import asyncio

        from src.api.routes.servers import members

        class _Db:
            def fetch_one(self, sql, params):
                return {"user_id": 7}

        servers = MagicMock()
        getattr(
            servers, "assign_role" if route_name.startswith("assign") else "remove_role"
        ).return_value = None

        def _drop(coro):
            coro.close()

        with (
            patch.object(members.api, "get_servers", return_value=servers),
            patch.object(members.api, "get_db", return_value=_Db()),
            patch.object(members, "_invalidate_roles_listing_cache"),
            patch.object(members, "invalidate_pattern") as invalidate,
            patch("asyncio.create_task", _drop),
        ):
            result = asyncio.run(
                getattr(members, route_name)("1", "10", "20", TokenInfo(7))
            )

        assert result.success is True
        patterns = {c.args[0] for c in invalidate.call_args_list}
        assert "server_members_api:*" in patterns

    def test_membership_lifecycle_invalidates_roster(self):
        from src.core.servers.manager.cache_ops import CacheOpsMixin

        ops = object.__new__(CacheOpsMixin)
        with (
            patch("src.core.database.cache_delete"),
            patch("src.core.database.invalidate_pattern") as invalidate,
            patch.object(ops, "_invalidate_permission_caches"),
        ):
            ops._invalidate_member_caches(1, 7)

        patterns = {c.args[0] for c in invalidate.call_args_list}
        assert "server_members_api:*" in patterns

    def test_kick_still_preserves_the_full_api_invalidation_set(self):
        """The refactor to the shared helper must not shrink kick's clears."""
        from src.api.routes.servers import members

        with patch.object(members, "invalidate_pattern") as invalidate:
            members._invalidate_member_access_caches()

        assert {c.args[0] for c in invalidate.call_args_list} == {
            "server_members_api:*",
            "user_servers_api:*",
            "server_channels_api:*",
            "server_stickers_api:*",
            "server_sounds_api:*",
            # Emoji list/detail pages embed per-member data as well, so a kick
            # clears them too; the set must not shrink silently.
            "emoji_list_api:*",
            "emoji_detail_api:*",
        }
