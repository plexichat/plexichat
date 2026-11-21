"""REGRESSION: unwired servers module must fail closed in the artifact-emit gate.

``_can_emit_artifact_message`` guards the inline MESSAGE_CREATE that artifact
creation posts straight through ``messaging.send_message``. It resolves the
servers module to decide whether the target conversation is backed by a server
channel that needs a live ``messages.send`` (plus view/timeout) grant. When the
module is unwired (``api.get_servers()`` returns ``None``) that lookup cannot be
evaluated. The gate used to return ``True`` there, skipping the guard and
letting any participant emit artifact content into a private-channel alias
without the send grant its own docstring says must be resolved.

Invariant (same as ``test_unwired_dependency_fails_closed``):

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The wired controls keep the invariant non-vacuous: fail-closed must not become
a blanket denial of DMs (no backing channel) or of event-only emissions (no
conversation at all).

Run with::

    pytest -n 0 -q src/tests/artifacts/test_unwired_servers_artifact_emit_fail_closed.py
"""

from __future__ import annotations

import pytest

import src.api as api
from src.api.routes import artifacts_helpers as helpers

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 7
CONV = 4242
CHANNEL = 100
SERVER = 200


class _WiredStore:
    """A wired servers store whose reverse lookup returns a given alias."""

    def __init__(self, alias):
        self.alias = alias

    def get_channel_by_conversation(self, conversation_id):
        return self.alias


@pytest.fixture
def unwired_servers(monkeypatch):
    """Make ``api.get_servers()`` report the servers module as unwired."""
    monkeypatch.setattr(api, "get_servers", lambda: None)
    return None


def test_unwired_servers_denies_artifact_message(unwired_servers):
    assert helpers._can_emit_artifact_message(CONV, USER) is False


def test_no_conversation_is_still_event_only_allowed(unwired_servers):
    """Control: no conversation to post into is allowed regardless of wiring."""
    assert helpers._can_emit_artifact_message(None, USER) is True


def test_wired_genuine_dm_still_allowed(monkeypatch):
    """Control: a wired store with no backing channel is still a DM/group."""
    monkeypatch.setattr(api, "get_servers", lambda: _WiredStore(alias=None))
    assert helpers._can_emit_artifact_message(CONV, USER) is True


def test_wired_channel_alias_still_gated(monkeypatch):
    """Control: with a wired alias the real send/view/timeout gate still denies."""
    monkeypatch.setattr(
        api,
        "get_servers",
        lambda: _WiredStore(alias={"id": CHANNEL, "server_id": SERVER}),
    )
    # No get_channel/has_permission helpers on the store -> cannot resolve the
    # grant -> must still fail closed.
    assert helpers._can_emit_artifact_message(CONV, USER) is False
