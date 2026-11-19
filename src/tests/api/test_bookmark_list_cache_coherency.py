"""Cache-coherency regressions for the per-credential bookmark list.

``GET /features/bookmarks`` used to re-read every bookmark row plus one
message per row on every poll of the bookmarks modal (1 + N queries), and
re-serialized the whole page. The read is now memoized behind a short TTL with
a per-credential key. These tests pin that:

1. a warm read is byte-for-byte identical and performs **no** backend queries;
2. credentials never share an entry (other users, and a bot vs its owner);
3. authorization is checked *before* the cache lookup, so both the bot-token
   403 and the conversation-participation 404 are uncached;
4. every bookmark mutation evicts all cached pages of the mutated credential;
5. failures (exceptions) are never cached, and the TTL is bounded.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.core.database import invalidate_pattern
from src.core.database.cache.base import mem_cache_matching_keys


class TokenInfo:
    """Stand-in with the real class name ``TokenInfo``.

    ``generate_cache_key`` special-cases that class name to key on
    ``user_id``; ``authenticated_cache_key`` additionally reads
    ``token_type`` / ``account_id``.
    """

    def __init__(self, user_id, token_type="user", account_id=None):
        self.user_id = user_id
        self.token_type = token_type
        self.account_id = account_id


def _rows(n=3, tag="a"):
    return [
        {
            "id": i,
            "user_id": 7,
            "message_id": 1000 + i,
            "conversation_id": 55,
            "label": f"{tag}{i}",
            "created_at": 1000 + i,
        }
        for i in range(1, n + 1)
    ]


class CountingDB:
    """Counts every storage call so query savings are measurable."""

    def __init__(self, rows=None):
        self.rows = list(rows if rows is not None else _rows())
        self.fetch_all_calls = 0
        self.fetch_one_calls = 0
        self.execute_calls = 0

    @property
    def queries(self):
        return self.fetch_all_calls + self.fetch_one_calls + self.execute_calls

    def fetch_all(self, sql, params=None):
        self.fetch_all_calls += 1
        return list(self.rows)

    def fetch_one(self, sql, params=None):
        self.fetch_one_calls += 1
        return None

    def execute(self, sql, params=None):
        self.execute_calls += 1
        return None


class FakeMessaging:
    """Per-row enrichment + the participant gate used by the pre-check."""

    def __init__(self, participant=True):
        self.get_message_calls = 0
        self.participant = participant
        self._participant_svc = self

    def is_participant(self, conversation_id, user_id):
        return self.participant

    def get_message(self, user_id, message_id):
        self.get_message_calls += 1
        return SimpleNamespace(content="body", author_id=9, created_at=1000)

    @property
    def queries(self):
        return self.get_message_calls


class FakeBookmarkService:
    """Stands in for the real service on the mutation routes."""

    instances = []

    def __init__(self, db, messaging=None):
        self.db = db
        self.messaging = messaging
        self.added = []
        self.removed = []
        FakeBookmarkService.instances.append(self)

    def get_bookmarks(self, user_id, conversation_id=None, limit=50):
        return self.db.fetch_all(
            "SELECT * FROM user_bookmarks WHERE user_id = ?", (user_id,)
        )

    def add_bookmark(self, user_id, message_id, conversation_id, label=None):
        self.added.append((user_id, message_id, conversation_id))
        return {"id": 1, "user_id": user_id, "message_id": message_id}

    def remove_bookmark(self, user_id, message_id):
        self.removed.append((user_id, message_id))
        return True


def _noop_create_task(coro):
    # The mutation routes fire a websocket event; close the coroutine so no
    # pending-task warning leaks into the next test.
    coro.close()
    return None


def _wire(monkeypatch, db, messaging, *, fake_service=False):
    from src.api.routes.features import bookmarks

    monkeypatch.setattr(bookmarks.api, "get_db", lambda: db)
    monkeypatch.setattr(bookmarks.api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(
        bookmarks, "asyncio", SimpleNamespace(create_task=_noop_create_task)
    )
    if fake_service:
        FakeBookmarkService.instances = []
        monkeypatch.setattr(bookmarks, "BookmarkService", FakeBookmarkService)
    return bookmarks


def _call(method, **kwargs):
    result = method(**kwargs)
    return asyncio.run(result) if inspect.isawaitable(result) else result


PREFIX = "bookmarks_api"


@pytest.fixture(autouse=True)
def _isolate_bookmark_cache():
    invalidate_pattern(f"{PREFIX}:*")
    assert mem_cache_matching_keys(f"{PREFIX}:*") == []
    yield
    invalidate_pattern(f"{PREFIX}:*")


# --- Key partitioning ------------------------------------------------------


def test_keys_partition_by_user_credential_and_page():
    from src.api.routes.features import bookmarks

    builder = bookmarks.authenticated_cache_key(PREFIX)
    common = {
        "user_id": 7,
        "conversation_id": None,
        "limit": 50,
    }
    owner = builder(current_user=TokenInfo(7), **common)
    other = builder(current_user=TokenInfo(8), **{**common, "user_id": 8})
    bot_of_owner = builder(
        current_user=TokenInfo(7, token_type="bot", account_id=500), **common
    )
    other_page = builder(current_user=TokenInfo(7), **{**common, "limit": 25})

    assert len({owner, other, bot_of_owner, other_page}) == 4
    assert owner.startswith(PREFIX)
    assert ":current_user:TokenInfo:7:" in owner
    assert ":bot:500:7:" in bot_of_owner


# --- Identical output, fewer queries ---------------------------------------


def test_warm_read_is_identical_and_costs_no_queries(monkeypatch):
    db, messaging = CountingDB(), FakeMessaging()
    bookmarks = _wire(monkeypatch, db, messaging)
    token = TokenInfo(7)

    cold = _call(
        bookmarks.list_bookmarks,
        conversation_id=None,
        limit=50,
        current_user=token,
    )
    cold_queries = db.queries + messaging.queries

    db.rows = _rows(tag="changed-behind-cache")
    warm = _call(
        bookmarks.list_bookmarks,
        conversation_id=None,
        limit=50,
        current_user=token,
    )
    warm_queries = db.queries + messaging.queries - cold_queries

    assert cold == warm
    assert len(cold["bookmarks"]) == 3
    # 1 row query + 3 enrichment queries cold, zero on the warm hit.
    assert cold_queries == 4
    assert warm_queries == 0


def test_warm_hit_is_served_from_process_local_l1(monkeypatch):
    db, messaging = CountingDB(), FakeMessaging()
    bookmarks = _wire(monkeypatch, db, messaging)

    _call(bookmarks.list_bookmarks, current_user=TokenInfo(7))
    assert mem_cache_matching_keys(f"{PREFIX}:*"), "read should warm the L1 tier"

    # With Valkey unavailable the L1 tier is the only tier; a second read is a
    # pure memory hit and adds no queries at all.
    before = db.queries + messaging.queries
    _call(bookmarks.list_bookmarks, current_user=TokenInfo(7))
    assert db.queries + messaging.queries == before


# --- No cross-user / bot-owner leak ----------------------------------------


def test_no_cross_user_cache_reuse(monkeypatch):
    db, messaging = CountingDB(_rows(tag="owner")), FakeMessaging()
    bookmarks = _wire(monkeypatch, db, messaging)

    owner = _call(bookmarks.list_bookmarks, current_user=TokenInfo(7))
    # Change the backing rows: another credential must not replay the owner's
    # cached page.
    db.rows = _rows(tag="other")
    other = _call(bookmarks.list_bookmarks, current_user=TokenInfo(8))

    assert owner["bookmarks"][0]["label"] == "owner1"
    assert other["bookmarks"][0]["label"] == "other1"
    # Two independent cold reads (4 queries each).
    assert db.queries + messaging.queries == 8


def test_bot_cannot_replay_owner_page_and_denial_is_uncached(monkeypatch):
    db, messaging = CountingDB(_rows(tag="private")), FakeMessaging()
    bookmarks = _wire(monkeypatch, db, messaging)

    owner = _call(bookmarks.list_bookmarks, current_user=TokenInfo(7))
    assert owner["bookmarks"][0]["label"] == "private1"

    bot = TokenInfo(7, token_type="bot", account_id=500)
    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            _call(bookmarks.list_bookmarks, current_user=bot)
        assert exc.value.status_code == 403

    # The gate ran on both attempts: no backend query was charged and no bot
    # cache entry was ever created (the owner's entry is untouched).
    assert db.queries + messaging.queries == 4
    assert mem_cache_matching_keys(f"{PREFIX}:bot:*") == []
    assert len(mem_cache_matching_keys(f"{PREFIX}:*")) == 1


# --- Authz denials are never cached -----------------------------------------


def test_conversation_denial_is_uncached(monkeypatch):
    db = CountingDB(_rows())
    messaging = FakeMessaging(participant=False)
    bookmarks = _wire(monkeypatch, db, messaging)

    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            _call(
                bookmarks.list_bookmarks,
                conversation_id="55",
                current_user=TokenInfo(7),
            )
        assert exc.value.status_code == 404
        assert not mem_cache_matching_keys(f"{PREFIX}:*"), "denial was cached"

    # Once access is granted the request succeeds; the earlier 404s did not
    # poison the cache.
    messaging.participant = True
    result = _call(
        bookmarks.list_bookmarks, conversation_id="55", current_user=TokenInfo(7)
    )
    assert result["bookmarks"]


def test_service_failure_is_never_cached(monkeypatch):
    db, messaging = CountingDB(), FakeMessaging()
    bookmarks = _wire(monkeypatch, db, messaging, fake_service=True)

    class _FailingService(FakeBookmarkService):
        def get_bookmarks(self, *args, **kwargs):
            raise RuntimeError("transient backend failure")

    monkeypatch.setattr(bookmarks, "BookmarkService", _FailingService)
    with pytest.raises(RuntimeError):
        _call(bookmarks.list_bookmarks, current_user=TokenInfo(7))
    assert not mem_cache_matching_keys(f"{PREFIX}:*")

    monkeypatch.setattr(bookmarks, "BookmarkService", FakeBookmarkService)
    result = _call(bookmarks.list_bookmarks, current_user=TokenInfo(7))
    assert result["bookmarks"]


# --- Invalidation on every mutation -----------------------------------------


def test_mutations_invalidate_all_pages_of_the_credential_only(monkeypatch):
    db, messaging = CountingDB(), FakeMessaging()
    bookmarks = _wire(monkeypatch, db, messaging, fake_service=True)

    page_a = dict(conversation_id=None, limit=50, current_user=TokenInfo(7))
    page_b = dict(conversation_id=None, limit=25, current_user=TokenInfo(7))
    other = dict(conversation_id=None, limit=50, current_user=TokenInfo(8))

    _call(bookmarks.list_bookmarks, **page_a)
    _call(bookmarks.list_bookmarks, **page_b)
    _call(bookmarks.list_bookmarks, **other)
    assert len(mem_cache_matching_keys(f"{PREFIX}:*")) == 3

    # Both mutation routes must evict every page of user 7 (not user 8).
    body = SimpleNamespace(
        message_id="1001",
        conversation_id="55",
        label=None,
        url=None,
    )
    _call(bookmarks.add_bookmark, body=body, current_user=TokenInfo(7))
    keys = mem_cache_matching_keys(f"{PREFIX}:*")
    assert len(keys) == 1, keys
    assert ":TokenInfo:8:" in keys[0]

    _call(bookmarks.list_bookmarks, **page_a)
    _call(bookmarks.list_bookmarks, **page_b)
    _call(bookmarks.remove_bookmark, message_id="1001", current_user=TokenInfo(7))
    assert mem_cache_matching_keys(f"{PREFIX}:*") == [
        k for k in mem_cache_matching_keys(f"{PREFIX}:*") if ":TokenInfo:8:" in k
    ]
    assert FakeBookmarkService.instances[-1].removed == [(7, 1001)]


def test_invalidated_read_reflects_new_backend_state(monkeypatch):
    db, messaging = CountingDB(_rows(tag="stale")), FakeMessaging()
    bookmarks = _wire(monkeypatch, db, messaging, fake_service=True)

    first = _call(bookmarks.list_bookmarks, current_user=TokenInfo(7))
    assert first["bookmarks"][0]["label"] == "stale1"

    db.rows = _rows(tag="fresh")
    _call(
        bookmarks.add_bookmark,
        body=SimpleNamespace(
            message_id="1001", conversation_id="55", label=None, url=None
        ),
        current_user=TokenInfo(7),
    )
    fresh = _call(bookmarks.list_bookmarks, current_user=TokenInfo(7))
    assert fresh["bookmarks"][0]["label"] == "fresh1"


# --- Bounded lifetime -------------------------------------------------------


def test_cache_ttls_are_bounded():
    from src.api.routes.features import bookmarks

    assert 0 < bookmarks._BOOKMARK_LIST_L1_TTL_SECONDS <= 3
    assert 0 < bookmarks._BOOKMARK_LIST_TTL_SECONDS <= 30
    assert (
        bookmarks._BOOKMARK_LIST_L1_TTL_SECONDS <= bookmarks._BOOKMARK_LIST_TTL_SECONDS
    )
