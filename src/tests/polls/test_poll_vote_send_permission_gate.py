"""SECURITY: poll mutations require ``messages.send`` in the backing channel.

A peer gated poll *reads* (detail/results/voters) on a live ``channels.view``
grant via ``_can_view_poll_message``.  Poll *writes* funnel through the same
``get_poll`` gate, so a member denied view is blocked -- but a member who CAN
view a read-only channel is still allowed to vote on, change, remove, create,
close and delete polls in it.  Conversation participation for a server channel
is granted to every server member, and ``channels.view`` says nothing about
the right to post, so a read-only member can mutate poll outcomes in a channel
they are not allowed to post to.

The forwarding and voice routes already add the independent ``messages.send``
requirement for exactly this reason.  Poll writes must do the same: fail closed
when a backing server channel exists and the caller lacks ``messages.send``.
A DM/group (no backing channel) needs no further gate because participation is
the only boundary that exists there.

Run with::

    pytest src/tests/polls/test_poll_vote_send_permission_gate.py -q -n 0
"""

import pytest

from src.core.polls.exceptions import PermissionDeniedError
from src.core.polls.manager import post_gate, reading as reading_module
from src.core.polls.manager.base import PollManagerBase
from src.core.polls.manager.creation import CreationMixin
from src.core.polls.manager.lifecycle import LifecycleMixin
from src.core.polls.manager.reading import ReadingMixin
from src.core.polls.manager.voting import VotingMixin
from src.core.polls.models import PollResultsVisibility
from src.core.polls.read_cache import PollReadCache

pytestmark = [pytest.mark.unit, pytest.mark.security]

POLL = 8300
MSG = 8301
CONV = 8302
CHANNEL = 8303
SERVER = 8304
VOTER = 42
OPT = 9000

POLL_ROW = {
    "id": POLL,
    "message_id": MSG,
    "question": "read-only-channel-poll",
    "question_encrypted": None,
    "created_by": VOTER,
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
    """DB double that records every write so denials can be proven pre-write."""

    def __init__(self):
        self.writes = []
        self.began = False
        self.committed = False

    def fetch_one(self, sql, params=()):
        if "SELECT message_id FROM poll_polls" in sql:
            return {"message_id": MSG}
        if "SELECT id FROM poll_polls WHERE message_id" in sql:
            return None
        if "FROM poll_polls" in sql:
            return dict(POLL_ROW)
        if "SELECT 1 FROM poll_voters" in sql:
            return None  # no prior vote registration
        if "SUM(CASE WHEN user_id = ?" in sql:
            return {"count": 1, "mine": 1}
        if "FROM poll_voters" in sql:
            return {"count": 1, "mine": 1}
        if "FROM poll_votes" in sql:
            return {"id": 1}
        if "COUNT(*) AS count FROM poll_options" in sql:
            return {"count": 1} if params and int(params[-1]) == OPT else {"count": 0}
        return None

    def fetch_all(self, sql, params=()):
        if "SELECT id FROM poll_options WHERE poll_id" in sql:
            return [{"id": OPT}] if params and int(params[-1]) == OPT else []
        if "FROM poll_options" in sql:
            return [dict(row) for row in OPTION_ROWS]
        if "FROM poll_votes" in sql:
            return [{"option_id": OPT, "count": 1, "mine": 1}]
        return []

    def begin_transaction(self):
        self.began = True

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def execute(self, sql, params=(), auto_commit=True):
        self.writes.append(sql)
        return _Cursor()

    def execute_many(self, sql, rows, auto_commit=True):
        self.writes.append(sql)
        return _Cursor()


class _Cursor:
    rowcount = 1

    def close(self):
        pass


class _Msg:
    def __init__(self, message_id):
        self.id = message_id
        self.conversation_id = CONV
        self.author_id = VOTER


class _Messaging:
    def get_message(self, user_id, message_id):
        return _Msg(message_id)


class _Servers:
    """View is always granted; ``messages.send`` is granted only to ``senders``."""

    def __init__(self, senders=()):
        self.senders = set(senders)
        self.permission_checks = []

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL, "server_id": SERVER}

    def get_channel(self, channel_id, user_id):
        return object()  # channels.view granted

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.permission_checks.append((user_id, server_id, permission, channel_id))
        return permission == "messages.send" and int(user_id) in self.senders


class _NoServers:
    """Servers module with no permission API (must fail closed for channels)."""

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL, "server_id": SERVER}

    def get_channel(self, channel_id, user_id):
        return object()


class _Manager:
    can_access_poll = ReadingMixin.can_access_poll
    get_poll = ReadingMixin.get_poll
    _row_to_poll = ReadingMixin._row_to_poll
    _voter_stats = ReadingMixin._voter_stats
    _has_voted = ReadingMixin._has_voted
    _voter_count = ReadingMixin._voter_count
    _can_view_results = ReadingMixin._can_view_results
    create_poll = CreationMixin.create_poll
    vote = VotingMixin.vote
    change_vote = VotingMixin.change_vote
    remove_vote = VotingMixin.remove_vote
    get_results = VotingMixin.get_results
    close_poll = LifecycleMixin.close_poll
    delete_poll = LifecycleMixin.delete_poll
    _end_poll = LifecycleMixin._end_poll
    _poll_read_key = PollManagerBase._poll_read_key
    _invalidate_poll_read_cache = PollManagerBase._invalidate_poll_read_cache
    _encrypt_polls = False

    def __init__(self, db, servers):
        self._db = db
        self._messaging = _Messaging()
        self._servers = servers
        self._poll_read_cache = PollReadCache()
        self._config = {"min_options": 2, "max_options": 10}

    def _validate_question(self, question):
        return question

    def _validate_option(self, option):
        return option

    def _validate_duration(self, duration_hours):
        return duration_hours

    def _require_messaging(self):
        return self._messaging

    def _generate_id(self):
        return 12345

    def _get_timestamp(self):
        return 1000

    def _emit_poll_update(self, poll, actor_id, action):
        pass


def _wire(monkeypatch, servers):
    # Reads (view gate) and writes (send gate) each resolve the servers module
    # lazily and patchably, via their own module-level provider.
    monkeypatch.setattr(reading_module, "_get_servers_module", lambda: servers)
    monkeypatch.setattr(post_gate, "_get_servers_module", lambda: servers)


class TestPollWriteSendGate:
    def test_vote_denied_without_send_permission_no_write(self, monkeypatch):
        servers = _Servers(senders=set())  # view granted, send denied
        _wire(monkeypatch, servers)
        db = _DB()

        with pytest.raises(PermissionDeniedError):
            _Manager(db, servers).vote(VOTER, POLL, [OPT])

        assert db.writes == []
        assert db.began is False
        assert (VOTER, SERVER, "messages.send", CHANNEL) in servers.permission_checks

    def test_vote_allowed_with_send_permission(self, monkeypatch):
        servers = _Servers(senders={VOTER})
        _wire(monkeypatch, servers)
        db = _DB()

        results = _Manager(db, servers).vote(VOTER, POLL, [OPT])

        assert results.user_voted is True
        assert db.committed is True

    def test_change_vote_denied_without_send_permission_no_write(self, monkeypatch):
        servers = _Servers(senders=set())
        _wire(monkeypatch, servers)
        db = _DB()

        with pytest.raises(PermissionDeniedError):
            _Manager(db, servers).change_vote(VOTER, POLL, [OPT])

        assert db.writes == []
        assert db.began is False

    def test_remove_vote_denied_without_send_permission_no_write(self, monkeypatch):
        servers = _Servers(senders=set())
        _wire(monkeypatch, servers)
        db = _DB()

        with pytest.raises(PermissionDeniedError):
            _Manager(db, servers).remove_vote(VOTER, POLL)

        assert db.writes == []
        assert db.began is False

    def test_close_and_delete_denied_without_send_permission(self, monkeypatch):
        servers = _Servers(senders=set())
        _wire(monkeypatch, servers)
        db = _DB()
        mgr = _Manager(db, servers)

        with pytest.raises(PermissionDeniedError):
            mgr.close_poll(VOTER, POLL)
        with pytest.raises(PermissionDeniedError):
            mgr.delete_poll(VOTER, POLL)

        assert db.writes == []

    def test_create_poll_denied_without_send_permission_no_write(self, monkeypatch):
        servers = _Servers(senders=set())
        _wire(monkeypatch, servers)
        db = _DB()

        with pytest.raises(PermissionDeniedError):
            _Manager(db, servers).create_poll(VOTER, MSG, "question?", ["a", "b"])

        assert db.writes == []
        assert db.began is False

    def test_missing_permission_api_fails_closed(self, monkeypatch):
        _wire(monkeypatch, _NoServers())
        db = _DB()

        with pytest.raises(PermissionDeniedError):
            _Manager(db, _NoServers()).vote(VOTER, POLL, [OPT])

        assert db.writes == []

    def test_dm_poll_needs_no_send_permission(self, monkeypatch):
        class _DMServers:
            def get_channel_by_conversation(self, conversation_id):
                return None  # no backing channel => genuine DM/group

        _wire(monkeypatch, _DMServers())
        db = _DB()

        results = _Manager(db, _DMServers()).vote(VOTER, POLL, [OPT])

        assert results.user_voted is True
