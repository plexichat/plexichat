"""SECURITY: poll reads must enforce a live channels.view grant.

Every poll read path (poll detail, results and voter roster) ultimately gates
on ``polls.can_access_poll`` / ``PollManager.get_poll``, which used to rely
solely on ``messaging.get_message``. That only proves conversation
*participation*, which for a server channel is granted to every member of the
server regardless of the per-channel ``channels.view`` permission. A member
denied view on a private channel could therefore read its question, options
and vote counts (and, as creator, its voter roster) by poll id.

These tests pin the boundary: a viewer WITH channels.view can read poll data,
a member denied view cannot, DMs need no channel grant, and a broken reverse
lookup fails closed. Run with:

    pytest src/tests/polls/test_poll_channel_view_gate.py -q -n 0
"""

import pytest

from src.core.polls.exceptions import PollNotFoundError
from src.core.polls.manager import reading as reading_module
from src.core.polls.manager import PollManager
from src.core.polls.models import PollResultsVisibility

pytestmark = [pytest.mark.unit, pytest.mark.security]

POLL = 8100
MSG = 8101
CONV = 8102
CHANNEL = 8103
VIEWER = 42
OPT = 9000

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
    """Minimal DB double: one poll, one option, one vote by the viewer."""

    def fetch_one(self, sql, params=()):
        if "SELECT message_id FROM poll_polls" in sql:
            return {"message_id": MSG}
        if "FROM poll_polls" in sql:
            return dict(POLL_ROW)
        if "FROM poll_voters" in sql:
            mine = 1 if params and int(params[0]) == VIEWER else 0
            return {"count": 1, "mine": mine}
        return None

    def fetch_all(self, sql, params=()):
        if "FROM poll_options" in sql:
            return [dict(row) for row in OPTION_ROWS]
        if "FROM poll_votes" in sql:
            return [{"option_id": OPT, "count": 1, "mine": 1}]
        if "SELECT user_id FROM poll_voters" in sql:
            return [{"user_id": VIEWER}]
        return []

    def execute(self, sql, params=(), auto_commit=True):
        return None


class _Messaging:
    """Participant gate is satisfied; get_message returns a channel message."""

    def __init__(self):
        self.calls = []

    def get_message(self, user_id, message_id):
        self.calls.append((user_id, message_id))
        return {"id": message_id, "conversation_id": CONV}


class _Servers:
    def __init__(self, *, viewable=(), backing=True, lookup_raises=False):
        self.viewable = set(viewable)
        self.backing = backing
        self.lookup_raises = lookup_raises
        self.channel_gets = []

    def get_channel_by_conversation(self, conversation_id):
        if self.lookup_raises:
            raise RuntimeError("reverse lookup down")
        if not self.backing:
            return None
        return {"id": CHANNEL, "server_id": 1}

    def get_channel(self, channel_id, user_id):
        self.channel_gets.append((channel_id, user_id))
        return object() if channel_id in self.viewable else None


class _NoLookup:
    """A servers module with no reverse-lookup capability."""


def _wire_servers(monkeypatch, servers):
    """Point the gate's patchable servers provider at our fake module."""
    monkeypatch.setattr(reading_module, "_get_servers_module", lambda: servers)


def _manager(messaging):
    return PollManager(_DB(), messaging_module=messaging)


class TestPollChannelViewGate:
    def test_can_access_denied_when_channel_view_missing(self, monkeypatch):
        servers = _Servers(viewable=set())
        _wire_servers(monkeypatch, servers)
        messaging = _Messaging()

        assert _manager(messaging).can_access_poll(POLL, VIEWER) is False
        assert (CHANNEL, VIEWER) in servers.channel_gets

    def test_can_access_allowed_when_channel_view_granted(self, monkeypatch):
        servers = _Servers(viewable={CHANNEL})
        _wire_servers(monkeypatch, servers)
        messaging = _Messaging()

        assert _manager(messaging).can_access_poll(POLL, VIEWER) is True
        assert (CHANNEL, VIEWER) in servers.channel_gets

    def test_get_poll_denied_when_channel_view_missing(self, monkeypatch):
        servers = _Servers(viewable=set())
        _wire_servers(monkeypatch, servers)

        assert _manager(_Messaging()).get_poll(POLL, VIEWER) is None

    def test_get_poll_allowed_when_channel_view_granted(self, monkeypatch):
        servers = _Servers(viewable={CHANNEL})
        _wire_servers(monkeypatch, servers)

        poll = _manager(_Messaging()).get_poll(POLL, VIEWER)
        assert poll is not None
        assert poll.question == "private-channel-poll"
        assert [o.text for o in poll.options] == ["option-a"]

    def test_results_denied_when_channel_view_missing(self, monkeypatch):
        servers = _Servers(viewable=set())
        _wire_servers(monkeypatch, servers)

        with pytest.raises(PollNotFoundError):
            _manager(_Messaging()).get_results(POLL, VIEWER)

    def test_voters_denied_when_channel_view_missing(self, monkeypatch):
        # The viewer is the poll creator, so the roster permission gate passes;
        # only the missing channel view may deny this read.
        servers = _Servers(viewable=set())
        _wire_servers(monkeypatch, servers)

        with pytest.raises(PollNotFoundError):
            _manager(_Messaging()).get_voters(POLL, VIEWER)

    def test_dm_needs_no_channel_view(self, monkeypatch):
        servers = _Servers(viewable=set(), backing=False)
        _wire_servers(monkeypatch, servers)

        assert _manager(_Messaging()).can_access_poll(POLL, VIEWER) is True
        assert servers.channel_gets == []

    def test_broken_reverse_lookup_fails_closed(self, monkeypatch):
        servers = _Servers(viewable={CHANNEL}, lookup_raises=True)
        _wire_servers(monkeypatch, servers)

        assert _manager(_Messaging()).can_access_poll(POLL, VIEWER) is False

    def test_missing_reverse_lookup_fails_closed(self, monkeypatch):
        _wire_servers(monkeypatch, _NoLookup())

        assert _manager(_Messaging()).can_access_poll(POLL, VIEWER) is False
