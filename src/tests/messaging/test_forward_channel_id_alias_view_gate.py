"""SECURITY: the forward view gate must not treat a channel-id alias as a DM.

``ForwardingService._require_channel_view`` resolves the backing channel with
``get_channel_by_conversation(conversation_id)``.  When that lookup returns
``None`` it "tolerates the unresolvable channel" and defers to
``is_participant``.  That is correct for a genuine DM, but not for the other
id shape the messaging layer accepts: a *channel id* used in place of the
channel's linked conversation id.

That alias really occurs in production:

* ``messages_crud/base.py`` stores ``conversation_id = channel_id`` for a
  server channel whose conversation link is missing and cannot be repaired.
* ``ParticipantService.is_participant`` grants every server member
  participation in that id via ``srv_channels.id`` (``WHERE conversation_id
  = ? OR id = ?``), regardless of ``channels.view``.
* The forward target id is caller-supplied, so a channel id can be passed
  there too.

``get_channel_by_conversation`` only matches ``srv_channels.conversation_id``,
so for the alias it returns ``None`` and the old fallback waved the request
through -- a member denied ``channels.view`` could decrypt and exfiltrate a
private message via ``/forward`` (or plant one in a read-only channel).

Run with: pytest src/tests/messaging/test_forward_channel_id_alias_view_gate.py -q -n 0
"""

import pytest

from src.core.messaging.services.forwarding import ForwardingService

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

MESSAGE_ID = 20
CHANNEL_ID = 555  # used as the message's conversation_id (dangling link)
DM_CONV = 99
AUTHOR = 7
VIEWER = 5

# explicit SELECT column order used by forward_message
_MESSAGE_ROW = (MESSAGE_ID, CHANNEL_ID, AUTHOR, "private-secret-content", None, 100)


class _DB:
    def __init__(self):
        self.writes = []

    def fetch_one(self, query, params=()):
        if "FROM msg_messages" in query:
            return _MESSAGE_ROW
        if "COUNT(*)" in query:
            return (0,)
        return None

    def fetch_all(self, query, params=()):
        return []

    def execute(self, query, params=(), auto_commit=True):
        self.writes.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


class _Participants:
    """Server member: participant in the channel-alias id and the DM target."""

    def is_participant(self, conversation_id, user_id):
        # Mirrors the srv_channels.id fallback in ParticipantService.
        return conversation_id in (CHANNEL_ID, DM_CONV)


class _Servers:
    """Reverse lookup keyed only by conversation_id, as in production."""

    def __init__(self, viewable):
        self.viewable = viewable
        self.channel_gets = []

    def get_channel_by_conversation(self, conversation_id):
        # No srv_channels row has conversation_id == CHANNEL_ID; the link is
        # dangling, so the reverse lookup reports "unresolvable".
        return None

    def channel_exists(self, channel_id):
        # Membership-agnostic: the id really is a channel, even though the
        # reverse-by-conversation lookup could not resolve it.
        return channel_id == CHANNEL_ID

    def get_channel(self, channel_id, user_id):
        self.channel_gets.append((channel_id, user_id))
        return object() if channel_id in self.viewable else None


def _service(servers, sent):
    db = _DB()
    svc = ForwardingService(db, None, _Participants(), servers)
    svc._safe_send_via_messaging = lambda *a, **k: sent.append(a) or {"id": 123}
    return db, svc


class TestForwardChannelIdAliasViewGate:
    def test_channel_alias_source_denies_view_blocks_exfiltration(self):
        """The fail-open: unresolvable reverse lookup must NOT mean 'DM'."""
        servers = _Servers(viewable=set())
        sent = []
        db, svc = _service(servers, sent)

        with pytest.raises(PermissionError):
            svc.forward_message(VIEWER, MESSAGE_ID, DM_CONV)

        assert sent == [], "private content must not be forwarded out"
        assert db.writes == [], "no forward record written"
        assert (CHANNEL_ID, VIEWER) in servers.channel_gets, "live view gate ran"

    def test_channel_alias_source_with_view_grant_allows_forward(self):
        """Control: a viewer who can see the aliased channel may still forward."""
        servers = _Servers(viewable={CHANNEL_ID})
        sent = []
        db, svc = _service(servers, sent)

        result = svc.forward_message(VIEWER, MESSAGE_ID, DM_CONV)

        assert result["new_message_id"] == 123
        assert len(db.writes) == 1
        assert (CHANNEL_ID, VIEWER) in servers.channel_gets

    def test_real_dm_still_skips_channel_gate(self):
        """Control: a genuine DM (no channel row) is still allowed."""

        class _DMServers(_Servers):
            def channel_exists(self, channel_id):
                return False

        servers = _DMServers(viewable=set())
        sent = []
        db, svc = _service(servers, sent)

        result = svc.forward_message(VIEWER, MESSAGE_ID, DM_CONV)

        assert result["new_message_id"] == 123
        assert len(db.writes) == 1
