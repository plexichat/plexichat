"""REGRESSION: unwired servers must fail closed in the reaction view gate.

``_require_channel_view`` in ``api/routes/reactions.py`` guards reaction writes
because the core reaction manager treats ``messages.read`` (channel view) as a
bit separate from participation and ``messages.add_reactions``. Participation
is seeded for every server member regardless of ``channels.view``, and a
``srv_channels.conversation_id`` alias can back a message in a private channel
while the servers module is unwired. The gate used to ``return`` (allow) when
the module was unwired, so a member denied view could still react there; the
unwired dependency must DENY, not skip.

Invariant (same as ``test_unwired_dependency_fails_closed``):

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The wired controls keep this non-vacuous: fail-closed must not become a
blanket denial of genuine DM/group reactions.

Run with::

    pytest -n 0 -q src/tests/api/test_unwired_servers_reactions_view_gate.py
"""

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.reactions import _require_channel_view

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
MID = 6000  # message id
CONV = 6100  # conversation id backing the message
CHANNEL = 7000
SERVER = 8000


class _ReactionStub:
    """Minimal reactions service: the message lives in a known conversation."""

    def get_conversation_id_from_message(self, message_id):
        return CONV if message_id == MID else None


class _WiredGenuineDM:
    """Wired store with no backing channel: a genuine DM/group."""

    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredPrivateChannelNoView:
    """Wired store whose alias maps to a channel the actor cannot view."""

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL, "server_id": SERVER}

    def has_permission(self, user_id, server_id, permission, channel_id):
        assert (user_id, server_id, permission, channel_id) == (
            USER,
            SERVER,
            "messages.read",
            CHANNEL,
        )
        return False  # channel view denied -> fail closed


class _WiredStubWithoutReverseLookup:
    """Wired manager object with no reverse-lookup API (existing control)."""

    pass


def test_unwired_denies_reaction_view_gate(monkeypatch):
    """An unwired servers module must deny, not skip, the view gate."""
    monkeypatch.setattr(api, "get_servers", lambda: None)
    assert api.get_servers() is None
    with pytest.raises(HTTPException) as ei:
        _require_channel_view(_ReactionStub(), USER, MID, "react to")
    assert ei.value.status_code == 403


def test_wired_genuine_dm_still_allowed(monkeypatch):
    """Control: a wired store with no backing channel still allows a DM."""
    monkeypatch.setattr(api, "get_servers", lambda: _WiredGenuineDM())
    _require_channel_view(_ReactionStub(), USER, MID, "react to")  # no raise


def test_wired_private_channel_without_view_denies(monkeypatch):
    """Control: a wired alias still fails closed without messages.read."""
    monkeypatch.setattr(api, "get_servers", lambda: _WiredPrivateChannelNoView())
    with pytest.raises(HTTPException) as ei:
        _require_channel_view(_ReactionStub(), USER, MID, "react to")
    assert ei.value.status_code == 403


def test_wired_stub_without_reverse_lookup_still_allowed(monkeypatch):
    """Control: existing wired-stub behaviour is unchanged by the fix."""
    monkeypatch.setattr(api, "get_servers", lambda: _WiredStubWithoutReverseLookup())
    _require_channel_view(_ReactionStub(), USER, MID, "react to")  # no raise
