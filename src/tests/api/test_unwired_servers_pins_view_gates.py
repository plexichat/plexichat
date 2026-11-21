"""REGRESSION: unwired servers must fail closed in both pins view gates.

``pins.py`` has two gates: ``_require_channel_view`` (the URL id) and
``_require_message_channel_view`` (the target message's backing channel, which
the URL id may alias). Both ``return``-ed (allowed) on a falsy ``servers_mod``,
so a member denied ``channels.view`` could pin/unpin private-channel messages
by passing the channel id or its linked ``conversation_id`` in the URL.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_pins_view_gates.py
"""

import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from src.api.routes.messages.pins import (
    _require_channel_view,
    _require_message_channel_view,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
CONV = 6000
CHANNEL = 7000


class _NoBackingChannel:
    """Wired store: no server-channel signal -> genuine DM/group."""

    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredAliasNoGrant:
    """Wired store: alias exists but the live view grant cannot be resolved."""

    def channel_exists(self, channel_id):
        return True

    def get_channel(self, channel_id, user_id):
        return None  # view denied / unresolvable

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL}


_MESSAGE = SimpleNamespace(conversation_id=CONV)


class TestAddressedIdGate:
    def test_unwired_denies(self):
        with pytest.raises(HTTPException) as ei:
            _require_channel_view(CONV, USER, None)
        assert ei.value.status_code == 404

    def test_wired_genuine_dm_still_allowed(self):
        _require_channel_view(CONV, USER, _NoBackingChannel())  # no raise

    def test_wired_alias_unresolvable_grant_denies(self):
        with pytest.raises(HTTPException) as ei:
            _require_channel_view(CONV, USER, _WiredAliasNoGrant())
        assert ei.value.status_code == 404


class TestMessageBackingChannelGate:
    def test_unwired_denies(self):
        with pytest.raises(HTTPException) as ei:
            _require_message_channel_view(_MESSAGE, CONV, USER, None)
        assert ei.value.status_code == 404

    def test_wired_genuine_dm_still_allowed(self):
        _require_message_channel_view(_MESSAGE, CONV, USER, _NoBackingChannel())

    def test_wired_alias_unresolvable_grant_denies(self):
        with pytest.raises(HTTPException) as ei:
            _require_message_channel_view(_MESSAGE, CONV, USER, _WiredAliasNoGrant())
        assert ei.value.status_code == 404
