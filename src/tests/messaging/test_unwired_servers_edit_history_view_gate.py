"""REGRESSION: unwired servers must fail closed in the edit-history view gate.

``EditHistoryService._require_channel_view`` guards reads that decrypt and
return *previous message content*. ``srv_channels.conversation_id`` aliases
live in the DB independently of the servers module, so a member denied
``channels.view`` can still reach a private channel's history in the unwired
state. The gate used to ``return`` (allow) on a falsy ``servers`` dependency.

Invariant: a check that cannot be evaluated because its dependency is unwired
must DENY, not skip. Wired/genuine-DM controls are non-vacuous: a wired store
whose reverse lookup finds no backing channel still allows the read.

Run with::

    pytest -n 0 -q src/tests/messaging/test_unwired_servers_edit_history_view_gate.py
"""

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError
from src.core.messaging.services.edit_history import EditHistoryService

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
MSG = 5000
CONV = 6000
CHANNEL = 7000
PRIOR_SECRET = "private-channel-prior-content"


class _MessageRepo:
    def get_by_id(self, message_id):
        return {"id": message_id, "conversation_id": CONV}


class _HistoryRepo:
    """The protected prior content still present in the DB."""

    def get_by_message(self, message_id, limit=None):
        return [
            {
                "id": 1,
                "message_id": message_id,
                "editor_id": 9,
                "old_content": PRIOR_SECRET,
                "edit_timestamp": 1700000000000,
                "version_number": 1,
                "old_content_encrypted": None,
            }
        ]


class _Participants:
    # Server membership seeds participation regardless of channels.view.
    def is_participant(self, conversation_id, user_id):
        return True


class _NoBackingChannel:
    """Wired store: the conversation is a genuine DM/group."""

    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredAliasNoGrant:
    """Wired store: alias resolves to a channel but the grant is unresolved."""

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL}

    # no get_channel -> cannot prove channels.view -> must deny


def _svc(servers):
    svc = EditHistoryService(object(), _Participants(), servers_mod=servers)
    svc._message_repo = _MessageRepo()
    svc._repo = _HistoryRepo()
    return svc


def test_unwired_denies_gate():
    with pytest.raises(ConversationAccessDeniedError):
        _svc(None)._require_channel_view(CONV, USER)


def test_unwired_does_not_leak_prior_content():
    """Pre-fix, get_edit_history returned PRIOR_SECRET while servers was None."""
    with pytest.raises(ConversationAccessDeniedError):
        _svc(None).get_edit_history(USER, MSG)


def test_wired_genuine_dm_still_allowed():
    _svc(_NoBackingChannel())._require_channel_view(CONV, USER)  # no raise


def test_wired_alias_unresolvable_grant_denies():
    with pytest.raises(ConversationAccessDeniedError):
        _svc(_WiredAliasNoGrant())._require_channel_view(CONV, USER)
