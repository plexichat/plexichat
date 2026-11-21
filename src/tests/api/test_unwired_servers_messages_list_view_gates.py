"""REGRESSION: unwired servers must fail closed in both message-list view gates.

``messages_list.py`` has two gates: the sync ``_require_channel_messages_view``
(page body) and the async ``_verify_pinned_channel_access`` (runs before the
120s cached pins body). Both skipped their denial on a falsy ``servers_mod``,
so a member denied ``channels.view`` could page through a private channel's
messages or keep receiving a warm private pin list after access was revoked.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_messages_list_view_gates.py
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.messages.messages_list import (
    _require_channel_messages_view,
    _verify_pinned_channel_access,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
CONV = 6000
CHANNEL = 7000


class _NoBackingChannel:
    """Wired store: no backing channel -> genuine DM/group."""

    def get_channel(self, channel_id, user_id):
        return None

    def channel_exists(self, channel_id):
        return False

    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredAliasNoGrant:
    """Wired store: alias resolves but the live view grant is unresolvable."""

    def get_channel(self, channel_id, user_id):
        return None

    def channel_exists(self, channel_id):
        return True

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL}


class TestMessageListViewGate:
    def test_unwired_denies(self):
        with pytest.raises(HTTPException) as ei:
            _require_channel_messages_view(CONV, USER, None)
        assert ei.value.status_code == 404

    def test_wired_genuine_dm_still_allowed(self):
        _require_channel_messages_view(CONV, USER, _NoBackingChannel())

    def test_wired_alias_unresolvable_grant_denies(self):
        with pytest.raises(HTTPException) as ei:
            _require_channel_messages_view(CONV, USER, _WiredAliasNoGrant())
        assert ei.value.status_code == 404


class TestPinnedChannelCacheGate:
    @pytest.mark.asyncio
    async def test_unwired_denies(self, monkeypatch):
        monkeypatch.setattr(api, "get_servers", lambda: None)
        with pytest.raises(HTTPException) as ei:
            await _verify_pinned_channel_access(
                str(CONV), SimpleNamespace(user_id=USER)
            )
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_wired_genuine_dm_still_allowed(self, monkeypatch):
        monkeypatch.setattr(api, "get_servers", lambda: _NoBackingChannel())
        await _verify_pinned_channel_access(str(CONV), SimpleNamespace(user_id=USER))

    @pytest.mark.asyncio
    async def test_wired_alias_unresolvable_grant_denies(self, monkeypatch):
        monkeypatch.setattr(api, "get_servers", lambda: _WiredAliasNoGrant())
        with pytest.raises(HTTPException) as ei:
            await _verify_pinned_channel_access(
                str(CONV), SimpleNamespace(user_id=USER)
            )
        assert ei.value.status_code == 404
