"""REGRESSION: unwired servers must fail closed in the attachment send gate.

``_require_attachment_send_access`` in ``api/routes/channels/attachments.py``
guards attachment uploads addressed by a server channel's linked
``conversation_id`` (an alias ``get_channel`` cannot resolve as a channel id).
It reverse-resolves the alias and requires a live view, timeout and
``messages.send`` grant. When the servers module is unwired that reverse lookup
cannot be evaluated, but the ``srv_channels.conversation_id`` row still exists
in the DB, so skipping the gate let any participant upload into a private
channel whose grants were never checked.

Invariant (same as ``test_unwired_dependency_fails_closed``):

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The wired controls keep this non-vacuous: fail-closed must not become a
blanket denial of genuine DM/group uploads.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_attachment_send_gate.py
"""

import pytest

from src.api.routes.channels.attachments import _require_attachment_send_access

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
ADDR = 6000  # conversation-id alias in the URL
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

    def get_channel(self, channel_id, user_id):
        return object()  # view grant present

    def is_timed_out(self, user_id, server_id):
        return False

    def has_permission(self, user_id, server_id, permission, channel_id):
        return False  # messages.send denied -> fail closed


def test_unwired_denies_attachment_send_gate():
    """An unwired servers module must deny, not skip, the send gate."""
    with pytest.raises(PermissionError):
        _require_attachment_send_access(None, USER, ADDR)


def test_wired_genuine_dm_still_allowed():
    """Control: a wired store with no backing channel still allows a DM."""
    _require_attachment_send_access(_WiredGenuineDM(), USER, ADDR)  # no raise


def test_wired_private_channel_without_send_grant_denies():
    """Control: a wired alias still fails closed without messages.send."""
    with pytest.raises(PermissionError):
        _require_attachment_send_access(_WiredPrivateChannelNoSend(), USER, ADDR)
