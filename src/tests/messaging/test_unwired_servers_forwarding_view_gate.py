"""REGRESSION: unwired servers must fail closed in the forwarding view gate.

``ForwardingService._require_channel_view`` guards forwarding, which reads and
decrypts the source message and re-sends it into a target conversation. A
``srv_channels.conversation_id`` alias can back the source conversation in the
DB while the servers module is unwired. The gate used to ``return`` (allow) on
a falsy ``servers`` dependency, so a member denied ``channels.view`` could
exfiltrate private-channel content into a channel/DM they control.

Invariant: a check that cannot be evaluated because its dependency is unwired
must DENY, not skip. Wired/genuine-DM controls stay non-vacuous.

Run with::

    pytest -n 0 -q src/tests/messaging/test_unwired_servers_forwarding_view_gate.py
"""

import pytest

from src.core.messaging.services.forwarding import ForwardingService

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
MSG = 5000
CONV = 6000
TARGET = 9000
CHANNEL = 7000
SECRET = "private-channel-secret-content"


class _DB:
    """The protected private-channel message still present in the DB."""

    def __init__(self):
        self.inserts = []

    def fetch_one(self, query, params=None):
        if "FROM msg_messages WHERE id" in query:
            return {
                "id": MSG,
                "conversation_id": CONV,
                "author_id": 9,
                "content": SECRET,
                "content_encrypted": None,
                "created_at": 1700000000000,
            }
        if "SELECT COUNT(*)" in query:
            return {"count": 0}
        return None

    def execute(self, query, params=None):
        self.inserts.append((query, params))


class _Participants:
    def is_participant(self, conversation_id, user_id):
        return True


class _NoBackingChannel:
    def get_channel_by_conversation(self, conversation_id):
        return None


class _WiredAliasNoGrant:
    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL}

    # no get_channel -> cannot prove channels.view -> must deny


def _svc(servers, db=None):
    db = db if db is not None else _DB()
    svc = ForwardingService(
        db,
        messaging_module=None,
        participant_svc=_Participants(),
        servers_mod=servers,
    )
    return svc, db


def test_unwired_denies_gate():
    svc, _ = _svc(None)
    with pytest.raises(PermissionError):
        svc._require_channel_view(CONV, USER)


def test_unwired_does_not_forward_private_content():
    """Pre-fix the forward proceeded and recorded SECRET into TARGET."""
    svc, db = _svc(None)
    with pytest.raises(PermissionError):
        svc.forward_message(USER, MSG, TARGET)
    assert db.inserts == []  # no forward record written for a denied view


def test_wired_genuine_dm_still_allowed():
    svc, _ = _svc(_NoBackingChannel())
    svc._require_channel_view(CONV, USER)  # no raise


def test_wired_alias_unresolvable_grant_denies():
    svc, _ = _svc(_WiredAliasNoGrant())
    with pytest.raises(PermissionError):
        svc._require_channel_view(CONV, USER)
