"""SECURITY: edit-history reads must enforce a live channels.view grant.

``EditHistoryService.get_edit_history`` (and ``get_edit_count``) previously
guarded only conversation *participation* (``is_participant``). For server
channels that grants every member of the server participation regardless of
the per-channel ``channels.view`` permission. Edit history decrypts and returns
*previous message content*, so a member denied view on a private channel could
read old versions of messages they are not allowed to see. The message get,
pin and forward paths already enforce the live ``get_channel`` view gate; the
edit-history read path did not.

Run with: pytest src/tests/messaging/test_edit_history_channel_view_gate.py -q -n 0
"""

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError
from src.core.messaging.services.edit_history import EditHistoryService

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

CONV_ID = 555
CHANNEL_ID = 555
MESSAGE_ID = 20
VIEWER = 5
AUTHOR = 7


class _DB:
    """Minimal DB double: message row + one edit-history row."""

    def __init__(self):
        self.history_reads = []
        self.count_reads = []

    def fetch_one(self, query, params=()):
        if "FROM msg_messages" in query:
            return {
                "id": MESSAGE_ID,
                "conversation_id": CONV_ID,
                "author_id": AUTHOR,
                "deleted": 0,
            }
        if "COUNT(*)" in query and "msg_edit_history" in query:
            self.count_reads.append(tuple(params))
            return {"count": 1}
        return None

    def fetch_all(self, query, params=()):
        if "msg_edit_history" in query:
            self.history_reads.append(tuple(params))
            return [
                {
                    "id": 1,
                    "message_id": MESSAGE_ID,
                    "editor_id": AUTHOR,
                    "old_content": "secret-prior-content",
                    "old_content_encrypted": None,
                    "edit_timestamp": 100,
                    "version_number": 1,
                }
            ]
        return []

    def execute(self, query, params=(), auto_commit=True):
        raise AssertionError("read path must not write")


class _Participants:
    """Server member: a participant in the conversation regardless of view."""

    def is_participant(self, conversation_id, user_id):
        return True


class _Servers:
    def __init__(self, channels, viewable):
        self.channels = channels  # conversation_id -> {"id": ...} or None
        self.viewable = viewable  # channel ids this user may view
        self.channel_gets = []

    def get_channel_by_conversation(self, conversation_id):
        return self.channels.get(conversation_id)

    def get_channel(self, channel_id, user_id):
        self.channel_gets.append((channel_id, user_id))
        return object() if channel_id in self.viewable else None


class TestEditHistoryChannelViewGate:
    def _svc(self, db, servers):
        return EditHistoryService(
            db,
            _Participants(),
            encryption_enabled=False,
            servers_mod=servers,
        )

    def test_history_read_denied_when_channel_view_missing(self):
        """The core leak: member of a private channel they cannot view."""
        db = _DB()
        servers = _Servers(
            {CONV_ID: {"id": CHANNEL_ID, "server_id": 1}}, viewable=set()
        )
        svc = self._svc(db, servers)

        with pytest.raises(ConversationAccessDeniedError):
            svc.get_edit_history(VIEWER, MESSAGE_ID)

        assert db.history_reads == []  # no prior content fetched/decrypted
        assert (CHANNEL_ID, VIEWER) in servers.channel_gets  # live gate ran

    def test_history_read_allowed_when_channel_view_granted(self):
        """Control: with channels.view the real prior content is returned."""
        db = _DB()
        servers = _Servers(
            {CONV_ID: {"id": CHANNEL_ID, "server_id": 1}}, viewable={CHANNEL_ID}
        )
        svc = self._svc(db, servers)

        history = svc.get_edit_history(VIEWER, MESSAGE_ID)

        assert len(history) == 1
        assert history[0]["old_content"] == "secret-prior-content"
        assert (CHANNEL_ID, VIEWER) in servers.channel_gets

    def test_dm_conversation_needs_no_channel_view(self):
        """A conversation with no backing channel is a DM/group, not a channel."""
        db = _DB()
        servers = _Servers({CONV_ID: None}, viewable=set())
        svc = self._svc(db, servers)

        history = svc.get_edit_history(VIEWER, MESSAGE_ID)

        assert len(history) == 1
        assert servers.channel_gets == []

    def test_missing_reverse_lookup_fails_closed(self):
        """A servers module that cannot map conversation->channel cannot be trusted."""

        class _NoLookup:
            pass

        db = _DB()
        svc = self._svc(db, _NoLookup())

        with pytest.raises(ConversationAccessDeniedError):
            svc.get_edit_history(VIEWER, MESSAGE_ID)

        assert db.history_reads == []

    def test_lookup_exception_fails_closed(self):
        """An exception in the reverse lookup must not fall open to a read."""

        class _BrokenLookup:
            def get_channel_by_conversation(self, conversation_id):
                raise RuntimeError("lookup down")

        db = _DB()
        svc = self._svc(db, _BrokenLookup())

        with pytest.raises(ConversationAccessDeniedError):
            svc.get_edit_history(VIEWER, MESSAGE_ID)

        assert db.history_reads == []

    def test_edit_count_denied_when_channel_view_missing(self):
        """The count endpoint is gated too (no metadata leak from private channels)."""
        db = _DB()
        servers = _Servers(
            {CONV_ID: {"id": CHANNEL_ID, "server_id": 1}}, viewable=set()
        )
        svc = self._svc(db, servers)

        with pytest.raises(ConversationAccessDeniedError):
            svc.get_edit_count(VIEWER, MESSAGE_ID)

        assert db.count_reads == []
        assert (CHANNEL_ID, VIEWER) in servers.channel_gets
