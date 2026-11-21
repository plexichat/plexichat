"""SECURITY: the poll channel-view gate must not treat a channel-id alias as a DM.

``_can_view_poll_message`` resolves the backing channel with
``get_channel_by_conversation(conversation_id)``.  When that lookup returns
``None`` it "tolerates the unresolvable channel" and defers to the messaging
participation check.  That is correct for a genuine DM, but NOT for a *channel
id* used in place of the channel's linked conversation id:

* ``messages_crud/base.py`` stores ``conversation_id = channel_id`` for a
  server channel whose conversation link is missing and cannot be repaired.
* ``ParticipantService.is_participant`` grants every server member
  participation in that id via ``srv_channels.id`` (``WHERE conversation_id
  = ? OR id = ?``), regardless of ``channels.view``.
* ``get_channel_by_conversation`` only matches ``srv_channels.conversation_id``,
  so for the alias it reports "unresolvable" and the gate used to return True.

That let a member denied ``channels.view`` on a private channel read its poll
question, options and vote counts by poll id.

Run with: pytest src/tests/polls/test_poll_channel_id_alias_view_gate.py -q -n 0
"""

import pytest

from src.core.polls.manager import reading as reading_module
from src.core.polls.manager import PollManager
from src.core.polls.models import PollResultsVisibility

pytestmark = [pytest.mark.unit, pytest.mark.security]

POLL = 8200
MSG = 8201
CHANNEL_ID = 8203  # used as the message's conversation_id (dangling link)
VIEWER = 42
OPT = 9100

POLL_ROW = {
    "id": POLL,
    "message_id": MSG,
    "question": "private-channel-poll",
    "question_encrypted": None,
    "created_by": VIEWER,
    "created_at": 100,
    "ends_at": None,
    "ended_at": None,
    "allow_multiple_choice": False,
    "results_visibility": PollResultsVisibility.ALWAYS.value,
}
OPTION_ROWS = [
    {
        "id": OPT,
        "poll_id": POLL,
        "text": "option-a",
        "text_encrypted": None,
        "position": 0,
    }
]


class _DB:
    def fetch_one(self, sql, params=()):
        if "SELECT message_id FROM poll_polls" in sql:
            return {"message_id": MSG}
        if "FROM poll_polls" in sql:
            return dict(POLL_ROW)
        if "FROM poll_voters" in sql:
            return {"count": 1, "mine": 1}
        return None

    def fetch_all(self, sql, params=()):
        if "FROM poll_options" in sql:
            return [dict(row) for row in OPTION_ROWS]
        if "FROM poll_votes" in sql:
            return [{"option_id": OPT, "count": 1, "mine": 1}]
        return []

    def execute(self, sql, params=(), auto_commit=True):
        return None


class _Messaging:
    """Participation passes; message carries a channel-id conversation_id."""

    def get_message(self, user_id, message_id):
        return {"id": message_id, "conversation_id": CHANNEL_ID}


class _Servers:
    """Reverse lookup keyed only by conversation_id, as in production."""

    def __init__(self, viewable=(), *, is_channel=True):
        self.viewable = set(viewable)
        self.is_channel = is_channel
        self.channel_gets = []

    def get_channel_by_conversation(self, conversation_id):
        # No srv_channels row has conversation_id == CHANNEL_ID, so the
        # reverse lookup reports "unresolvable" even though it is a channel.
        return None

    def channel_exists(self, channel_id):
        return self.is_channel and channel_id == CHANNEL_ID

    def get_channel(self, channel_id, user_id):
        self.channel_gets.append((channel_id, user_id))
        return object() if channel_id in self.viewable else None


def _manager(servers):
    from unittest.mock import patch

    with patch.object(reading_module, "_get_servers_module", lambda: servers):
        return PollManager(_DB(), messaging_module=_Messaging()).get_poll(POLL, VIEWER)


class TestPollChannelIdAliasViewGate:
    def test_channel_alias_denies_view_blocks_poll_leak(self):
        """The fail-open: an unresolvable reverse lookup must not mean 'DM'."""
        servers = _Servers(viewable=set())

        assert _manager(servers) is None, "private poll must not be returned"
        assert (CHANNEL_ID, VIEWER) in servers.channel_gets, "live view gate ran"

    def test_channel_alias_with_view_grant_allows_poll(self):
        """Control: a viewer who can see the aliased channel may read the poll."""
        servers = _Servers(viewable={CHANNEL_ID})

        poll = _manager(servers)

        assert poll is not None
        assert poll.question == "private-channel-poll"
        assert (CHANNEL_ID, VIEWER) in servers.channel_gets

    def test_real_dm_still_skips_channel_gate(self):
        """Control: a genuine DM (no channel row) is still allowed."""
        servers = _Servers(viewable=set(), is_channel=False)

        poll = _manager(servers)

        assert poll is not None
        assert servers.channel_gets == []
