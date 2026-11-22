"""Regression: bulk read-receipt writes must not probe each channel's settings.

BEFORE: ``mark_read_bulk`` called ``servers_mod.get_channel(conv, user_id)``
once per conversation solely to read ``read_receipts_enabled``. That helper is
not a cheap field read -- it checks server membership and ``channels.view`` on
every call -- so an N-channel bulk ack paid N redundant authorization/DB probes
on top of the batched status work the existing tests already cover.

AFTER: the flags for every conversation are resolved with one bounded ``IN``
query, defaulting to ``True`` for DMs / missing rows exactly like the legacy
``get_channel`` fallback, so a channel with receipts disabled is still skipped
for public status writes while its read position always advances.
"""

from types import SimpleNamespace

import pytest

from src.core.messaging.repositories.message_status import (
    MessageStatusRepository,
    _MAX_BULK_CHUNK,
)
from src.core.messaging.services.message_status import MessageStatusService

USER = 5001
CONVS = [7001, 7002, 7003, 7004, 7005, 7006]
DISABLED = {7002, 7005}
MAX_ID = 100


class _RecordingDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.fetch_calls = []

    def fetch_all(self, query, params):
        self.fetch_calls.append((query, params))
        return [r for r in self.rows if r["id"] in params]


class _FakeMessageRepo:
    def __init__(self):
        self.invalidated = []

    def get_max_ids_in_conversations(self, convs):
        return {c: MAX_ID for c in convs}

    def invalidate_conversation_cache(self, cid):
        self.invalidated.append(cid)


class _FakeStatusRepo:
    def __init__(self):
        self.settings_calls = []
        self.range_calls = []

    def get_read_receipts_enabled(self, convs):
        convs = list(convs)
        assert len(convs) <= _MAX_BULK_CHUNK, "IN fan-out must stay bounded"
        self.settings_calls.append(convs)
        return {c: c not in DISABLED for c in convs}

    def batch_mark_read_bulk(self, uid, ranges, ts):
        self.range_calls.append(list(ranges))
        return {conv: 1 for conv, _, _ in ranges}


class _FakeParticipantRepo:
    def __init__(self):
        self.position_updates = []

    def get_last_read_positions(self, uid, convs):
        return {c: 0 for c in convs}

    def update_last_read_bulk(self, updates):
        self.position_updates.append(list(updates))


class _FakeParticipantService:
    def are_participants_bulk(self, convs, uid):
        return {c: True for c in convs}

    def is_participant(self, cid, uid):
        return True


class _FakeServers:
    def __init__(self):
        self.get_channel_calls = []

    def get_channel(self, conversation_id, user_id):
        self.get_channel_calls.append(conversation_id)
        return SimpleNamespace(read_receipts_enabled=True)


class _FakeSettings:
    def get_message_settings(self, uid):
        return SimpleNamespace(read_receipts_enabled=True)


def _service(status_repo, participant_repo, message_repo, servers):
    svc = MessageStatusService(object(), _FakeParticipantService(), _FakeSettings())
    svc._repo = status_repo
    svc._participant_repo = participant_repo
    svc._message_repo = message_repo
    svc._get_servers_module = lambda: servers
    return svc


@pytest.mark.messaging
def test_bulk_ack_resolves_receipt_settings_in_one_call():
    status_repo = _FakeStatusRepo()
    participant_repo = _FakeParticipantRepo()
    message_repo = _FakeMessageRepo()
    servers = _FakeServers()

    counts = _service(
        status_repo, participant_repo, message_repo, servers
    ).mark_read_bulk(USER, [(cid, None) for cid in CONVS])

    # One bounded settings lookup for the whole bulk, never one get_channel
    # probe per conversation.
    assert len(status_repo.settings_calls) == 1
    assert sorted(status_repo.settings_calls[0]) == sorted(CONVS)
    assert servers.get_channel_calls == [], (
        "per-channel servers.get_channel probe must not survive in the bulk path"
    )

    # Channels with receipts disabled are skipped for PUBLIC status writes but
    # their read position still advances, matching the single-channel semantics.
    ranges = status_repo.range_calls[0]
    written = {conv for conv, _, _ in ranges}
    assert written == set(CONVS) - DISABLED
    assert {row[0] for row in participant_repo.position_updates[0]} == set(CONVS)
    assert sum(counts.values()) == len(CONVS) - len(DISABLED)


@pytest.mark.messaging
def test_receipt_settings_lookup_is_bounded_and_scoped():
    db = _RecordingDB(
        [{"id": c, "read_receipts_enabled": 0 if c in DISABLED else 1} for c in CONVS]
    )
    repo = MessageStatusRepository(db)

    flags = repo.get_read_receipts_enabled(CONVS + [CONVS[0]])

    assert flags == {c: c not in DISABLED for c in CONVS}
    assert len(db.fetch_calls) == 1, "all conversation ids in one bounded query"
    sql, params = db.fetch_calls[0]
    assert "srv_channels" in sql and "read_receipts_enabled" in sql
    assert len(params) == len(CONVS), "ids deduplicated before binding"
    assert len(params) <= _MAX_BULK_CHUNK, "bound IN list stays within the chunk cap"


@pytest.mark.messaging
def test_receipt_settings_lookup_chunks_large_conversation_sets():
    db = _RecordingDB([])
    repo = MessageStatusRepository(db)
    ids = list(range(1, _MAX_BULK_CHUNK * 2 + 3))

    assert repo.get_read_receipts_enabled(ids) == {}
    assert len(db.fetch_calls) == 3
    assert all(len(params) <= _MAX_BULK_CHUNK for _, params in db.fetch_calls)


@pytest.mark.messaging
def test_bulk_ack_settings_probes_stay_flat_as_audience_grows():
    """Per-channel setting probes must not scale with the bulk audience.

    ``mark_read_bulk`` used to call ``servers.get_channel`` once per
    conversation just to read ``read_receipts_enabled`` -- a membership and
    ``channels.view`` check per channel. Doubling the audience doubled those
    probes. With the bounded bulk fetch the count is one settings lookup and
    zero per-channel probes no matter how large the audience (below the chunk
    cap), so the query count stays flat.
    """

    def _probes(n):
        convs = list(range(10_000, 10_000 + n))
        status_repo = _FakeStatusRepo()
        participant_repo = _FakeParticipantRepo()
        message_repo = _FakeMessageRepo()
        servers = _FakeServers()
        _service(
            status_repo, participant_repo, message_repo, servers
        ).mark_read_bulk(USER, [(cid, None) for cid in convs])
        return len(servers.get_channel_calls), len(status_repo.settings_calls)

    # Before the fix: (4, 0) and (60, 0) -- the first number grows with N.
    # After the fix: exactly one bounded settings lookup and no get_channel
    # probes for either audience size.
    assert _probes(4) == (0, 1)
    assert _probes(60) == (0, 1)
