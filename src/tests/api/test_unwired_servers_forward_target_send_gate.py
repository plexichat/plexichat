"""REGRESSION: unwired servers must fail closed in the forward target gate.

``_require_target_send_access`` in ``api/routes/features/forwarding.py``
guards the forward target because ``messaging.send_message`` does not apply the
REST send route's ``messages.send`` gate. Conversation participation is seeded
for every server member, so participation alone is not a send grant. A
``srv_channels.conversation_id`` alias can back the target in the DB while the
servers module is unwired; skipping the gate then let a member forward into a
read-only channel whose ``messages.send`` grant was never checked.

Invariant (same as ``test_unwired_dependency_fails_closed``):

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The wired controls keep this non-vacuous: fail-closed must not become a
blanket denial of genuine DM/group forwards.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_forward_target_send_gate.py
"""

import pytest

from src.api.routes.features.forwarding import _require_target_send_access

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
TARGET = 6000  # conversation-id alias
CHANNEL = 7000
SERVER = 8000


class _WiredGenuineDM:
    """Wired store with no backing channel: a genuine DM/group."""

    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredPrivateChannelNoSend:
    """Wired store whose alias maps to a real channel without a send grant."""

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL, "server_id": SERVER}

    def has_permission(self, user_id, server_id, permission, channel_id):
        return False  # messages.send denied -> fail closed


def test_unwired_denies_forward_target_gate():
    """An unwired servers module must deny, not skip, the target send gate."""
    with pytest.raises(PermissionError):
        _require_target_send_access(None, USER, TARGET)


def test_wired_genuine_dm_still_allowed():
    """Control: a wired store with no backing channel still allows a DM."""
    _require_target_send_access(_WiredGenuineDM(), USER, TARGET)  # no raise


def test_wired_private_channel_without_send_grant_denies():
    """Control: a wired alias still fails closed without messages.send."""
    with pytest.raises(PermissionError):
        _require_target_send_access(_WiredPrivateChannelNoSend(), USER, TARGET)
