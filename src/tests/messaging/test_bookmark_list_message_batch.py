"""Regression: the bookmark listing must not run an N+1 of message lookups.

``BookmarkService.get_bookmarks`` used to call ``messaging.get_message`` once
per bookmark row. Each call pays a message-row query, a participant probe, a
pin query and an attachment query, so a 200-row page cost ~800 round trips.
The listing now resolves every message id in one bounded ``IN`` fan-out
(<=500 ids per query) through ``get_messages_by_ids``, with the participant
gate deduplicated per conversation.

These tests assert:
  * output parity with the legacy per-row enrichment (including the access
    gate that drops unviewable messages),
  * the listing issues exactly one bulk call and zero per-row calls,
  * the bulk resolver chunks every IN predicate at <=500 ids and its query
    count is bounded rather than proportional to the number of messages,
  * a legacy/stub messaging object without the bulk API still works.
"""

from types import SimpleNamespace


from src.core.messaging.services.bookmarks import BookmarkService
from src.core.messaging.services.message.retrieval import (
    RetrievalMixin,
    _BULK_ID_CHUNK,
)

USER = 777111


class _FakeDB:
    """Records the single bookmark-page query and returns canned rows."""

    def __init__(self, rows):
        self._rows = rows
        self.queries = []

    def fetch_all(self, query, params=None):
        self.queries.append((" ".join(query.split()), tuple(params or ())))
        return list(self._rows)

    def fetch_one(self, query, params=None):
        return None


def _bookmark_rows(n):
    return [
        {
            "id": 1000 + i,
            "user_id": USER,
            "message_id": 5000 + i,
            "conversation_id": 9000 + i,
            "label": None,
            "created_at": 1700000000000 + i,
        }
        for i in range(n)
    ]


def _message(mid, content="hello"):
    return SimpleNamespace(
        id=mid,
        content=content,
        author_id=42,
        created_at=1700000000001,
        conversation_id=9000,
        attachments=[],
    )


class _BulkMessaging:
    """Messaging stub exposing the new bulk resolver *and* the legacy one."""

    def __init__(self, messages):
        self._messages = messages
        self.bulk_calls = 0
        self.legacy_calls = 0
        self.bulk_arg_len = 0

    def get_messages_by_ids(self, user_id, ids):
        self.bulk_calls += 1
        self.bulk_arg_len = len(ids)
        # Mirror the real resolver: ids are normalized to int and the result
        # is keyed by int, even when the stored row carries a string id.
        out = {}
        for mid in ids:
            try:
                key = int(mid)
            except (TypeError, ValueError):
                continue
            if key in self._messages:
                out[key] = self._messages[key]
        return out

    def get_message(self, user_id, mid):
        self.legacy_calls += 1
        return self._messages.get(mid)


def test_listing_uses_one_bulk_call_and_matches_legacy_enrichment():
    n = 40
    rows = _bookmark_rows(n)
    # Every message is viewable except id 5003 -> that bookmark must be dropped.
    messages = {5000 + i: _message(5000 + i, content=f"m{i}") for i in range(n)}
    del messages[5003]

    bulk = _BulkMessaging(messages)
    got = BookmarkService(_FakeDB(rows), bulk).get_bookmarks(USER)

    assert bulk.bulk_calls == 1
    assert bulk.legacy_calls == 0
    assert bulk.bulk_arg_len == n
    assert len(got) == n - 1
    assert [r["message_id"] for r in got] == [5000 + i for i in range(n) if i != 3]
    assert got[0]["content_preview"] == "m0"
    assert got[0]["author_id"] == 42
    assert got[0]["message_created_at"] == 1700000000001

    # Legacy reference produces exactly the same list.
    legacy = _BulkMessaging(messages)
    legacy.get_messages_by_ids = None  # force the per-row fallback
    expected = BookmarkService(_FakeDB(rows), legacy).get_bookmarks(USER)
    assert got == expected
    assert legacy.legacy_calls == n


def test_bulk_resolver_chunks_in_predicates_and_bounds_queries():
    """1200 ids -> 3 chunks of <=500, not 1200 single-id queries."""
    n = 1200
    ids = [5000 + i for i in range(n)]
    # 4 conversations shared across all messages: participant gate is checked
    # once per conversation, not once per message.
    rows = [
        {
            "id": mid,
            "conversation_id": 9000 + (i % 4),
            "author_id": 42,
            "content": f"m{i}",
            "created_at": 1700000000000 + i,
        }
        for i, mid in enumerate(ids)
    ]
    by_id = {r["id"]: r for r in rows}

    class _MsgRepo:
        def __init__(self):
            self.batches = []

        def get_batch_by_ids(self, batch):
            self.batches.append(list(batch))
            return [by_id[i] for i in batch if i in by_id]

        def row_to_model(self, row, pin_info=None):
            return SimpleNamespace(
                id=row["id"],
                conversation_id=row["conversation_id"],
                author_id=row["author_id"],
                content=row["content"],
                created_at=row["created_at"],
                attachments=[],
            )

    class _PinRepo:
        def __init__(self):
            self.batches = []

        def get_batch_by_messages(self, batch):
            self.batches.append(list(batch))
            return {}

    class _AttRepo:
        def __init__(self):
            self.batches = []

        def get_batch_by_messages(self, batch):
            self.batches.append(list(batch))
            return {}

    class _Participants:
        def __init__(self):
            self.calls = []

        def is_participant(self, conv_id, uid):
            self.calls.append((conv_id, uid))
            return True

    repo, pins, atts, part = _MsgRepo(), _PinRepo(), _AttRepo(), _Participants()

    class _Svc(RetrievalMixin):
        pass

    svc = _Svc.__new__(_Svc)
    svc._repo = repo
    svc._pin_repo = pins
    svc._attachment_repo = atts
    svc._participant_svc = part

    result = svc.get_messages_by_ids(USER, ids)

    assert len(result) == n
    assert _BULK_ID_CHUNK == 500

    # Message rows: one IN query per <=500 chunk.
    assert [len(b) for b in repo.batches] == [500, 500, 200]
    assert all(len(b) <= 500 for b in repo.batches)
    # Pins/attachments: also chunked, also bounded.
    assert sum(len(b) for b in pins.batches) == n
    assert sum(len(b) for b in atts.batches) == n
    assert all(len(b) <= 500 for b in pins.batches + atts.batches)

    # Participant gate deduplicated per conversation.
    assert len(part.calls) == 4

    # Before: n message queries + n pin queries + n attachment queries + n
    # participant probes = 4n. After: a handful of bounded queries.
    before = 4 * n
    after = len(repo.batches) + len(pins.batches) + len(atts.batches) + len(part.calls)
    assert after <= 15
    assert after < before / 50


def test_bulk_resolver_drops_unviewable_messages():
    ids = [1, 2, 3]
    rows = [
        {
            "id": i,
            "conversation_id": 10 if i != 2 else 99,
            "author_id": 42,
            "content": "x",
            "created_at": 0,
        }
        for i in ids
    ]
    by_id = {r["id"]: r for r in rows}

    class _MsgRepo:
        def get_batch_by_ids(self, batch):
            return [by_id[i] for i in batch if i in by_id]

        def row_to_model(self, row, pin_info=None):
            return SimpleNamespace(id=row["id"], attachments=[])

    class _Empty:
        def get_batch_by_messages(self, batch):
            return {}

    class _Participants:
        def is_participant(self, conv_id, uid):
            return conv_id == 10

    class _Svc(RetrievalMixin):
        pass

    svc = _Svc.__new__(_Svc)
    svc._repo = _MsgRepo()
    svc._pin_repo = _Empty()
    svc._attachment_repo = _Empty()
    svc._participant_svc = _Participants()

    assert set(svc.get_messages_by_ids(USER, ids)) == {1, 3}


def test_legacy_messaging_without_bulk_api_still_enriches():
    """A stub that only exposes get_message keeps the old per-row path."""
    rows = _bookmark_rows(2)
    messages = {5000: _message(5000, "a"), 5001: _message(5001, "b")}

    class _Legacy:
        def __init__(self):
            self.calls = []

        def get_message(self, user_id, mid):
            self.calls.append(mid)
            return messages.get(mid)

    legacy = _Legacy()
    got = BookmarkService(_FakeDB(rows), legacy).get_bookmarks(USER)
    assert legacy.calls == [5000, 5001]
    assert [r["content_preview"] for r in got] == ["a", "b"]


def test_stored_string_message_id_matches_int_keyed_bulk_result():
    rows = _bookmark_rows(1)
    rows[0]["message_id"] = "5000"
    bulk = _BulkMessaging({5000: _message(5000, "str-id")})
    got = BookmarkService(_FakeDB(rows), bulk).get_bookmarks(USER)
    assert len(got) == 1
    assert got[0]["content_preview"] == "str-id"
