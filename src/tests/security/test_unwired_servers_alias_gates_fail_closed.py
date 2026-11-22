"""REGRESSION: unwired servers module must fail closed in the channel-alias gates.

The message view/delete and poll view/write gates resolve the servers module
to decide whether a conversation is backed by a server channel that needs a
live ``channels.view`` / ``messages.send`` grant.  When that module is unwired
(``None``) the lookup cannot be evaluated.  Returning ``True`` there skipped
the gate and let any participant read or mutate private-channel content in
that degraded state.

Invariant (same as ``test_unwired_dependency_fails_closed``):

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The wired/genuine-DM controls keep the invariant non-vacuous: fail-closed must
not become a blanket denial of DMs, which have no backing channel to gate.

Run with::

    pytest -n 0 -q src/tests/security/test_unwired_servers_alias_gates_fail_closed.py
"""

from __future__ import annotations

import pytest

from src.core.messaging.services import pin
from src.core.messaging.services.message import edit_delete
from src.core.polls.manager import post_gate
from src.core.polls.manager import reading as poll_reading

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
MESSAGE = 10
CONV = 123
CHANNEL = 456


class _Msg:
    conversation_id = CONV


class _Messaging:
    def get_message(self, user_id, message_id):
        return _Msg()


class _NoBackingChannel:
    """A wired store where the conversation is a genuine DM/group."""

    def get_channel_by_conversation(self, conversation_id):
        return None

    def channel_exists(self, channel_id):
        return False


class _PrivateChannelAliasNoGrant:
    """A wired store where the conversation aliases a private channel.

    The reverse lookup resolves the alias, but no live ``channels.view`` grant
    can be proven, so the gate must deny rather than fall through to allow.
    """

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL}

    def get_channel(self, channel_id, user_id):
        return None


_GATES = (
    (edit_delete._can_view_conversation_messages, (USER, CONV)),
    (poll_reading._can_view_poll_message, (_Messaging(), MESSAGE, USER)),
    (post_gate.can_post_poll_message, (_Messaging(), MESSAGE, USER)),
    (pin._can_view_conversation_messages, (USER, CONV)),
)


@pytest.mark.parametrize("gate,args", _GATES)
def test_unwired_servers_denies(monkeypatch, gate, args):
    for module in (edit_delete, poll_reading, post_gate, pin):
        monkeypatch.setattr(module, "_get_servers_module", lambda: None)

    assert gate(*args) is False, "unwired servers module must fail closed"


@pytest.mark.parametrize("gate,args", _GATES)
def test_wired_genuine_dm_still_allowed(monkeypatch, gate, args):
    """Control: a wired store with no backing channel is still a DM/group."""
    for module in (edit_delete, poll_reading, post_gate, pin):
        monkeypatch.setattr(module, "_get_servers_module", lambda: _NoBackingChannel())

    assert gate(*args) is True, "fail-closed must not blanket-deny genuine DMs"


def test_pin_gate_wired_private_channel_alias_denies_without_view(monkeypatch):
    """Non-vacuous control: a real private-channel alias must deny.

    The unwired denial is only meaningful if a wired store that actually
    resolves a backing channel still denies when the live view grant cannot be
    proven; otherwise fail-closed would be an unrelated blanket denial.
    """
    monkeypatch.setattr(
        pin, "_get_servers_module", lambda: _PrivateChannelAliasNoGrant()
    )

    assert pin._can_view_conversation_messages(USER, CONV) is False
