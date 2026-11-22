"""REGRESSION: unwired servers must fail closed in the attachment send gate.

``_require_attachment_send_access`` in ``api/routes/channels/attachments.py``
guards attachment uploads addressed by a server channel's linked
``conversation_id``. The route also accepts a bare conversation id as a URL
alias, and conversation *participation* is seeded for every server member
independent of ``messages.send``. Without this gate a member of a private
channel could upload an attachment "to" it through the alias, consuming
storage quota and planting content without a send grant.

The gate used to ``return`` (allow) when the servers module was unwired, even
though a backing private-channel alias can exist in the DB independently of
that module. Deny in that case; a wired store whose reverse lookup finds no
backing channel still allows a genuine DM upload below.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_attachment_send_gate.py
"""

import pytest

from src.api.routes.channels.attachments import _require_attachment_send_access

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
ADDR = 7000
CHANNEL = 7100
SERVER = 7200


class _NoBackingChannel:
    """Wired store: no server-channel signal at all -> genuine DM/group."""

    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredPrivateChannelNoSend:
    """Wired store: alias resolves to a channel with no send grant."""

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL, "server_id": SERVER}

    def get_channel(self, channel_id, user_id):
        return object() if channel_id == CHANNEL else None

    def is_timed_out(self, user_id, server_id):
        return False

    def has_permission(self, user_id, server_id, permission, channel_id):
        return False


def test_unwired_denies_attachment_send_gate():
    with pytest.raises(PermissionError):
        _require_attachment_send_access(None, USER, ADDR)


def test_wired_genuine_dm_still_allowed():
    _require_attachment_send_access(_NoBackingChannel(), USER, ADDR)  # no raise


def test_wired_private_channel_no_send_denies():
    with pytest.raises(PermissionError):
        _require_attachment_send_access(_WiredPrivateChannelNoSend(), USER, ADDR)
