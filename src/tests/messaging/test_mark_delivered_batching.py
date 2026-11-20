"""Regression: delivery-ack resolution must be batched and fail-closed.

BEFORE: ``mark_delivered`` issued one message-row SELECT, one status probe and
one participation probe *per id* -- a 3N fan-out on a delivery burst. AFTER:
message rows and caller statuses are fetched with one bounded IN query per
chunk each, and participation is resolved once per distinct conversation via
the shared bulk gate (which writes back into the same per-pair cache the
single check uses, so a revoked membership cannot leak a delivery write).
"""

from src.core.messaging.models import MessageStatusType
from src.core.messaging.services.message_status import MessageStatusService

USER = 5001
OTHER = 5002
CONV_OK = 7001
CONV_DENY = 7002
CHUNK = 500


class FakeMessageRepo:
    def __init__(self, rows):
        self.rows = {r["id"]: r for r in rows}
        self.by_id_calls = []
        self.batch_calls = []

    def get_by_id(self, mid):
        self.by_id_calls.append(mid)
        return self.rows.get(mid)

    def get_batch_by_ids(self, ids):
        ids = list(ids)
        assert len(ids) <= CHUNK, "IN fan-out must be bounded"
        self.batch_calls.append(ids)
        return [self.rows[i] for i in ids if i in self.rows]


class FakeStatusRepo:
    def __init__(self, statuses=None):
        self.statuses = dict(statuses or {})
        self.probe_calls = []
        self.batch_calls = []
        self.writes = []

    def get_by_message_and_user(self, mid, uid):
        self.probe_calls.append((mid, uid))
        return {"status": self.statuses.get((uid, mid), "sent")}

    def get_batch_for_user(self, uid, ids):
        ids = list(ids)
        assert len(ids) <= CHUNK, "IN fan-out must be bounded"
        self.batch_calls.append((uid, ids))
        return {
            mid: MessageStatusType(status)
            for (owner, mid), status in self.statuses.items()
            if owner == uid and mid in ids
        }

    def batch_mark_delivered(self, uid, ids, ts):
        self.writes.append((uid, list(ids)))
        return len(ids)


class FakeParticipantService:
    def __init__(self, allowed):
        self.allowed = set(allowed)
        self.single_calls = []
        self.bulk_calls = []

    def is_participant(self, cid, uid):
        self.single_calls.append((cid, uid))
        return cid in self.allowed

    def are_participants_bulk(self, cids, uid):
        cids = list(cids)
        self.bulk_calls.append((uid, cids))
        return {c: c in self.allowed for c in cids}


def _service(msg_repo, status_repo, part_svc):
    svc = MessageStatusService(object(), part_svc, object())
    svc._message_repo = msg_repo
    svc._repo = status_repo
    return svc


def _rows(count, conv, author):
    return [
        {"id": i, "conversation_id": conv, "author_id": author}
        for i in range(1, count + 1)
    ]


def test_mark_delivered_batches_lookups_and_gates_authz():
    rows = _rows(6, CONV_OK, OTHER) + [
        {"id": 90, "conversation_id": CONV_DENY, "author_id": OTHER},
        {"id": 91, "conversation_id": CONV_OK, "author_id": USER},
        {"id": 92, "conversation_id": CONV_OK, "author_id": 0},
    ]
    ids = [r["id"] for r in rows]
    # This user already READ msg 2 (skip). Another user's READ on msg 3 must
    # not hide it: the status probe has to stay scoped to the caller.
    statuses = {(USER, 2): "read", (OTHER, 3): "read"}
    msg_repo = FakeMessageRepo(rows)
    status_repo = FakeStatusRepo(statuses)
    part = FakeParticipantService({CONV_OK})

    count = _service(msg_repo, status_repo, part).mark_delivered(USER, ids)

    # 9 ids: before = 9 + 9 + 9 = 27 single lookups; after = 3 batch calls.
    assert len(ids) == 9
    assert msg_repo.by_id_calls == [], "per-id message SELECT must be gone"
    assert status_repo.probe_calls == [], "per-id status probe must be gone"
    assert len(msg_repo.batch_calls) == 1
    assert len(status_repo.batch_calls) == 1
    assert status_repo.batch_calls[0][0] == USER, "statuses scoped to caller"
    assert part.bulk_calls and part.bulk_calls[0][0] == USER
    assert part.single_calls == [], "participation must use the bulk gate"
    total = (
        len(msg_repo.by_id_calls)
        + len(status_repo.probe_calls)
        + len(part.single_calls)
        + sum(len(b) for b in msg_repo.batch_calls)
        + sum(len(b) for _, b in status_repo.batch_calls)
        + sum(len(b) for _, b in part.bulk_calls)
    )
    assert total == (
        len(msg_repo.batch_calls[0])
        + len(status_repo.batch_calls[0][1])
        + len(part.bulk_calls[0][1])
    ), "one pass per id (rows + statuses) plus one per distinct conversation"

    written = status_repo.writes[0][1]
    assert 1 in written and 3 in written
    assert 2 not in written, "already-read message skipped"
    assert 90 not in written, "non-participant conversation denied"
    assert 91 not in written, "own message skipped"
    assert 92 not in written, "system message skipped"
    assert count == len(written)


def test_mark_delivered_chunk_bound():
    rows = _rows(CHUNK + 5, CONV_OK, OTHER)
    msg_repo = FakeMessageRepo(rows)
    status_repo = FakeStatusRepo()
    part = FakeParticipantService({CONV_OK})

    count = _service(msg_repo, status_repo, part).mark_delivered(
        USER, [r["id"] for r in rows]
    )

    assert len(msg_repo.batch_calls) == 2
    assert len(status_repo.batch_calls) == 2
    assert count == CHUNK + 5


def test_mark_delivered_bulk_unavailable_falls_back_fail_closed():
    rows = _rows(2, CONV_DENY, OTHER)
    msg_repo = FakeMessageRepo(rows)
    status_repo = FakeStatusRepo()
    part = FakeParticipantService(set())
    part.are_participants_bulk = None  # legacy stub without the bulk helper

    count = _service(msg_repo, status_repo, part).mark_delivered(
        USER, [r["id"] for r in rows]
    )

    assert count == 0
    assert part.single_calls, "legacy path must still run the access gate"


def test_mark_delivered_bulk_error_fails_closed():
    rows = _rows(2, CONV_DENY, OTHER)
    msg_repo = FakeMessageRepo(rows)
    status_repo = FakeStatusRepo()
    part = FakeParticipantService(set())

    def boom(*_args, **_kwargs):
        raise RuntimeError("bulk gate unavailable")

    part.are_participants_bulk = boom

    count = _service(msg_repo, status_repo, part).mark_delivered(
        USER, [r["id"] for r in rows]
    )

    assert count == 0
    assert status_repo.writes == []
