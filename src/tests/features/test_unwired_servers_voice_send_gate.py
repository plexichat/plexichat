"""REGRESSION: unwired servers must fail closed in the voice send gate.

``_require_voice_send_access`` guards voice messages, which publish straight
through ``messaging.send_message`` and bypass the REST send route's
``messages.send`` / timeout / view gates. A ``srv_channels.conversation_id``
alias can back the target conversation in the DB while the servers module is
unwired. The gate used to ``return`` (allow) on a falsy ``servers`` dependency,
so a member denied send on a read-only/private channel could post a voice
message into it.

Invariant: a check that cannot be evaluated because its dependency is unwired
must DENY, not skip. A wired store with no backing channel still allows a
genuine DM/group.

Run with::

    pytest -n 0 -q src/tests/features/test_unwired_servers_voice_send_gate.py
"""

import pytest

from src.api.routes.features.voice import _require_voice_send_access

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
CONV = 6000
CHANNEL = 7000
SERVER = 8000


class _NoBackingChannel:
    """Wired store: the conversation is a genuine DM/group."""

    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredAliasNoGrant:
    """Wired store: alias resolves to a channel but permission data is missing."""

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL, "server_id": SERVER}
    # no get_channel / is_timed_out / has_permission -> cannot prove send grant


def test_unwired_denies_voice_send():
    with pytest.raises(PermissionError):
        _require_voice_send_access(None, USER, CONV)


def test_wired_genuine_dm_still_allowed():
    _require_voice_send_access(_NoBackingChannel(), USER, CONV)  # no raise


def test_wired_alias_unresolvable_grant_denies():
    with pytest.raises(PermissionError):
        _require_voice_send_access(_WiredAliasNoGrant(), USER, CONV)
