"""Invite-code resolution must use ONE batched lookup, not one query per code.

``MessagesCRUDBase._resolve_invite_codes`` runs on every message send. Before
this change it called ``servers_mod.get_invite(code)`` once per candidate
(up to ``AUTOMOD_MAX_INVITE_CODES`` = 10) whenever the per-code cache missed.
A message carrying ten novel invite codes therefore cost ten DB round trips on
the send hot path. The lookup is now a single set-based call, chunked to stay
below DB parameter limits, with the per-code getter kept only as a fallback for
manager implementations that do not expose the bulk helper.
"""

from unittest.mock import patch

import pytest

from src.api.routes.messages.messages_crud.base import MessagesCRUDBase

pytestmark = [pytest.mark.unit]

# Ten distinct invite codes -> capped at AUTOMOD_MAX_INVITE_CODES candidates.
CONTENT = " ".join(f"join discord.gg/code{i:04d}" for i in range(10))


class _SpyServers:
    """Manager double that counts per-code vs. batched invite resolution."""

    def __init__(self, existing):
        self.existing = set(existing)
        self.get_invite_calls = 0
        self.bulk_calls = 0

    def get_invite(self, code):
        self.get_invite_calls += 1
        return object() if code in self.existing else None

    def get_existing_invite_codes(self, codes):
        self.bulk_calls += 1
        return {c for c in codes if c in self.existing}


def _resolve(servers):
    base = MessagesCRUDBase()
    # Force every per-code cache lookup to miss so the resolver must hit the
    # manager. Patch the names where they are imported (module-level import).
    with (
        patch(
            "src.api.routes.messages.messages_crud.base.cache_get",
            return_value=None,
        ),
        patch("src.api.routes.messages.messages_crud.base.cache_set"),
    ):
        return base._resolve_invite_codes(CONTENT, servers)


def test_bulk_helper_resolves_all_codes_in_one_lookup():
    servers = _SpyServers(existing={"code0000", "code0009"})

    known = _resolve(servers)

    assert set(known) == {"code0000", "code0009"}
    assert servers.bulk_calls == 1, (
        f"expected one batched lookup, got {servers.bulk_calls}"
    )
    assert servers.get_invite_calls == 0, (
        "per-code get_invite must not be used when the bulk helper is present; "
        f"got {servers.get_invite_calls} calls (the pre-fix N+1)"
    )


def test_falls_back_to_bounded_per_code_lookup_without_bulk_helper():
    class _LegacyServers:
        def __init__(self):
            self.calls = 0

        def get_invite(self, code):
            self.calls += 1
            return object() if code == "code0005" else None

    legacy = _LegacyServers()

    known = _resolve(legacy)

    assert set(known) == {"code0005"}
    # Still bounded by AUTOMOD_MAX_INVITE_CODES, never unbounded.
    assert legacy.calls == MessagesCRUDBase.AUTOMOD_MAX_INVITE_CODES


def test_manager_bulk_lookup_returns_only_live_unrevoked_codes(server_with_channels):
    """The real manager method confirms existence without reviving revoked codes."""
    owner, _, _, server, general, _, servers = server_with_channels
    live = servers.create_invite(user_id=owner.id, channel_id=general.id)
    revoked = servers.create_invite(user_id=owner.id, channel_id=general.id)
    servers.delete_invite(owner.id, revoked.code)

    result = servers.get_existing_invite_codes(
        [live.code, revoked.code, "does-not-exist", live.code]
    )

    assert server.id == live.server_id
    assert result == {live.code}
