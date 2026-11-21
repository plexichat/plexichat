"""REGRESSION: unwired servers must fail closed in the edit channel-view gate.

``_require_edit_channel_view`` in ``api/routes/messages/messages_crud/edit.py``
guards message edits addressed either by a channel id or by the channel's
linked ``conversation_id`` alias. ``get_message`` plus the channel-scope check
only prove participation, which every server member has regardless of
``channels.view``. A ``srv_channels.conversation_id`` alias can back the URL id
in the DB while the servers module is unwired; skipping the gate then let a
member denied view edit a message in a private channel through either spelling
of the id.

Invariant (same as ``test_unwired_dependency_fails_closed``):

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The wired controls keep this non-vacuous: fail-closed must not become a
blanket denial of genuine DM/group edits.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_edit_channel_view_gate.py
"""

import pytest
from fastapi import HTTPException

from src.api.routes.messages.messages_crud.edit import _require_edit_channel_view

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
URL_CID = 6000
CONV = 6001  # authoritative conversation id backing the message
CHANNEL = 7000


class _WiredGenuineDM:
    """Wired store with no backing channel: a genuine DM/group."""

    def get_channel(self, channel_id, user_id):
        return None

    def get_channel_by_conversation(self, conversation_id):
        return None

    def channel_exists(self, channel_id):
        return False


class _WiredPrivateChannelNoView:
    """Wired store whose alias maps to a channel the caller cannot view."""

    def get_channel(self, channel_id, user_id):
        return None  # no live view grant

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL}

    def channel_exists(self, channel_id):
        return True


def test_unwired_denies_edit_channel_view_gate():
    """An unwired servers module must deny, not skip, the view gate."""
    with pytest.raises(HTTPException) as ei:
        _require_edit_channel_view(URL_CID, CONV, USER, None)
    assert ei.value.status_code == 404


def test_wired_genuine_dm_still_allowed():
    """Control: a wired store with no backing channel still allows a DM."""
    _require_edit_channel_view(URL_CID, CONV, USER, _WiredGenuineDM())  # no raise


def test_wired_private_channel_without_view_grant_denies():
    """Control: a wired alias still fails closed without channels.view."""
    with pytest.raises(HTTPException) as ei:
        _require_edit_channel_view(URL_CID, CONV, USER, _WiredPrivateChannelNoView())
    assert ei.value.status_code == 404
