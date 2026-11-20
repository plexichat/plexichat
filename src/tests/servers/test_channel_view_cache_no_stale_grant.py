"""Regression tests for the bounded, role-version-keyed channel-view cache.

Two properties are pinned here:

* the repeated ``channels.view`` checks produced by channel/message hydration
  collapse to one authoritative resolution (measurable reduction), and
* no revocation — channel override, role change, or kick — can be served from a
  stale cached grant. The decision memo is invalidated the moment the scope's
  role-version advances, so a denied viewer is denied on the very next check.
"""

import pytest

from src.core.servers.channel_access_cache import (
    ChannelAccessCache,
    get_channel_access_cache,
)


@pytest.fixture(autouse=True)
def _isolated_channel_view_cache():
    cache = get_channel_access_cache()
    cache.clear()
    yield
    cache.clear()


def _cache():
    return get_channel_access_cache()


class TestRepeatedCheckReduction:
    def test_repeated_channel_view_check_computed_once(
        self, server_with_channels, monkeypatch
    ):
        """A burst of identical channel-view checks pays one resolution."""
        (
            server,
            owner,
            admin,
            member,
            outsider,
            general,
            announcements,
            private,
            category,
            servers,
        ) = server_with_channels
        role_handler = servers.role_handler
        channel_id = int(general.id)
        n = 200

        calls = []

        def counting(*args, **kwargs):
            calls.append(1)
            return True

        # Baseline: the pre-optimization path recomputed the decision on every
        # check (no bounded L1 memo in front of the resolver).
        for _ in range(n):
            counting(owner.id, server.id, "channels.view", channel_id)
        before = len(calls)
        assert before == n

        # Optimized: the same burst through the public cached API.
        calls.clear()
        _cache().clear()
        monkeypatch.setattr(role_handler, "_compute_has_permission", counting)
        for _ in range(n):
            assert (
                role_handler.has_permission(
                    owner.id, server.id, "channels.view", channel_id
                )
                is True
            )
        after = len(calls)

        stats = _cache().stats()
        assert after == 1, f"expected 1 authoritative computation, got {after}"
        assert after <= before // 10, f"no meaningful reduction: {before} -> {after}"
        assert stats["hits"] == n - 1
        assert stats["misses"] == 1
        assert stats["puts"] == 1

    def test_key_separation(self, server_with_channels, monkeypatch):
        """Different user / channel / permission never share a decision."""
        (
            server,
            owner,
            admin,
            member,
            outsider,
            general,
            announcements,
            private,
            category,
            servers,
        ) = server_with_channels
        role_handler = servers.role_handler

        calls = []

        def counting(*args, **kwargs):
            calls.append(args)
            return True

        monkeypatch.setattr(role_handler, "_compute_has_permission", counting)
        _cache().clear()

        role_handler.has_permission(owner.id, server.id, "channels.view", general.id)
        role_handler.has_permission(owner.id, server.id, "channels.view", general.id)
        # Different permission...
        role_handler.has_permission(owner.id, server.id, "messages.read", general.id)
        # ...different channel...
        role_handler.has_permission(
            owner.id, server.id, "channels.view", announcements.id
        )
        # ...and different user are all distinct decisions.
        role_handler.has_permission(member.id, server.id, "channels.view", general.id)

        assert len(calls) == 4, calls
        assert _cache().stats()["misses"] == 4


class TestNoStaleGrant:
    def test_channel_override_revocation_is_immediate(self, server_with_channels):
        """A channel-view deny takes effect on the very next check."""
        (
            server,
            owner,
            admin,
            member,
            outsider,
            general,
            announcements,
            private,
            category,
            servers,
        ) = server_with_channels

        # Warm the cache with a GRANT for this (user, channel, permission).
        assert (
            servers.has_permission(member.id, server.id, "channels.view", general.id)
            is True
        )
        assert (
            servers.has_permission(member.id, server.id, "channels.view", general.id)
            is True
        )
        warm_stats = _cache().stats()
        assert warm_stats["hits"] >= 1

        # Revoke channels.view for this member on this channel.
        servers.set_channel_override(
            owner.id,
            general.id,
            "member",
            member.id,
            deny={"channels.view": True},
        )

        # The old grant must NOT be replayed from the cache after the bump.
        assert (
            servers.has_permission(member.id, server.id, "channels.view", general.id)
            is False
        )

    def test_kick_revokes_cached_channel_view(self, server_with_channels):
        """A membership change invalidates the member's cached decisions."""
        (
            server,
            owner,
            admin,
            member,
            outsider,
            general,
            announcements,
            private,
            category,
            servers,
        ) = server_with_channels

        assert (
            servers.has_permission(member.id, server.id, "channels.view", general.id)
            is True
        )
        servers.kick_member(owner.id, server.id, member.id)

        assert (
            servers.has_permission(member.id, server.id, "channels.view", general.id)
            is False
        )

    def test_role_grant_and_revocation_are_immediate(self, server_with_channels):
        """Assigning then removing a role retires cached channel decisions."""
        (
            server,
            owner,
            admin,
            member,
            outsider,
            general,
            announcements,
            private,
            category,
            servers,
        ) = server_with_channels

        # "messages.manage" is NOT in the default @everyone role, so the grant
        # and its revocation are unambiguous. Pass channel_id so the check
        # routes through the bounded channel-scoped cache.
        view_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Manager",
            permissions={"messages.manage": True},
        )
        servers.assign_role(owner.id, server.id, member.id, view_role.id)
        assert (
            servers.has_permission(member.id, server.id, "messages.manage", private.id)
            is True
        )
        servers.remove_role(owner.id, server.id, member.id, view_role.id)
        assert (
            servers.has_permission(member.id, server.id, "messages.manage", private.id)
            is False
        )


class TestFailClosedAndBounds:
    def test_cache_error_falls_back_to_authoritative_result(
        self, server_with_channels, monkeypatch
    ):
        """A broken memo can only force recomputation, never a grant."""
        (
            server,
            owner,
            admin,
            member,
            outsider,
            general,
            announcements,
            private,
            category,
            servers,
        ) = server_with_channels
        role_handler = servers.role_handler

        def boom(*args, **kwargs):
            raise RuntimeError("cache down")

        monkeypatch.setattr(_cache(), "get", boom)
        monkeypatch.setattr(
            role_handler,
            "_compute_has_permission",
            lambda *a, **k: False,
        )
        assert (
            role_handler.has_permission(
                member.id, server.id, "channels.view", general.id
            )
            is False
        )

    def test_bounded_lru_and_explicit_bump(self):
        """The memo is entry-bounded and version bumps retire old decisions."""
        cache = ChannelAccessCache(max_entries=8, ttl_seconds=60)
        for i in range(100):
            cache.put(1, 2, 1000 + i, "channels.view", True)
        assert len(cache) <= 8

        # The most recently written entry survives the LRU eviction.
        assert cache.get(1, 2, 1099, "channels.view") is True
        old_version = cache.version(1, 2)
        cache.bump(1, 2)
        assert cache.version(1, 2) != old_version
        # After the bump the old decision is unreachable (fail-closed miss).
        assert cache.get(1, 2, 1099, "channels.view") is None
