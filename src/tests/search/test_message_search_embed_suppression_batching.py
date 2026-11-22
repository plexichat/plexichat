"""PERF: message search resolves embed suppression in one batched query.

BEFORE: ``search_messages`` called ``_message_to_response(result,
viewer_user_id=...)`` for every hit. With ``suppress_embeds`` left at its
default ``None``, the serializer fell back to one ``are_embeds_suppressed()``
SQL query per message: a 100-hit page issued 100 near-identical queries just to
discover that none (or a handful) were suppressed.

AFTER: the page's id list is resolved through ``batch_embed_suppressed_ids`` in
a single chunked ``IN (...)`` query and the result is passed explicitly to the
serializer, which skips the per-id fallback entirely. The tests below show the
before (N per-id calls) and after (one batched call) against the same fake DB,
and pin result parity, the bounded id set (no cross-user ids in the query), and
the fail-closed fallback when the batch query is unavailable.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.api.routes import search as search_route
from src.api.routes.messages import helpers as message_helpers
from src.core.search.models import MessageSearchResult, MessageSearchResultPage

pytestmark = [pytest.mark.unit, pytest.mark.search]

N_HITS = 8
FIRST_ID = 1000
SUPPRESSED_IDS = {FIRST_ID + 1, FIRST_ID + 5}


def _page() -> MessageSearchResultPage:
    return MessageSearchResultPage(
        results=[
            MessageSearchResult(
                id=FIRST_ID + i,
                message_id=FIRST_ID + i,
                author_id=7,
                conversation_id=9,
                content="needle",
                created_at=1000 + i,
                has_embeds=True,
            )
            for i in range(N_HITS)
        ]
    )


class _CountingEmbedsDB:
    """Fake embeds DB: one batched query, plus the legacy per-id lookup."""

    def __init__(self, suppressed_ids=SUPPRESSED_IDS, fail_batch=False):
        self.suppressed_ids = set(suppressed_ids)
        self.fail_batch = fail_batch
        self.batch_calls = []
        self.per_id_calls = []

    def fetch_all(self, sql, params=()):
        self.batch_calls.append((sql, tuple(params)))
        if self.fail_batch:
            raise RuntimeError("batch query unavailable")
        rows = []
        for mid in params:
            if int(mid) in self.suppressed_ids:
                rows.append({"message_id": int(mid), "total": 1, "suppressed_count": 1})
        return rows

    def are_embeds_suppressed(self, mid) -> bool:
        self.per_id_calls.append(int(mid))
        return int(mid) in self.suppressed_ids


class _FakeEmbeds:
    """Production ``api.get_embeds()`` wiring: a module-like object with ``_db``."""

    def __init__(self, db):
        self._db = db

    def are_embeds_suppressed(self, mid) -> bool:
        return self._db.are_embeds_suppressed(mid)

    def _get_manager(self):
        return self


class _FakeSearch:
    def __init__(self, page):
        self._page = page
        self.calls = []

    def search_messages_page(self, **kwargs):
        self.calls.append(kwargs)
        return self._page


def _run_route(db, page=None):
    """Call the real route with fakes; return (response, fake_search)."""
    fake_search = _FakeSearch(page or _page())
    fake_embeds = _FakeEmbeds(db)
    current_user = SimpleNamespace(user_id=42)
    with (
        patch.object(search_route.api, "get_search", return_value=fake_search),
        patch.object(search_route.api, "get_embeds", return_value=fake_embeds),
        patch.object(search_route.api, "get_polls", return_value=None),
    ):
        response = asyncio.run(
            search_route.search_messages(
                query="needle",
                conversation_id=None,
                server_id=None,
                channel_id=None,
                author_id=None,
                limit=25,
                cursor=None,
                current_user=current_user,
            )
        )
    return response, fake_search


def test_before_and_after_query_counts():
    """N per-id queries before; one batched query and zero per-id after."""
    page = _page()

    # BEFORE: serialize each hit the way the route used to (no suppress flag),
    # so the serializer performs its one-off lookup per message.
    before_db = _CountingEmbedsDB()
    fake_embeds = _FakeEmbeds(before_db)
    with (
        patch.object(search_route.api, "get_embeds", return_value=fake_embeds),
        patch.object(search_route.api, "get_polls", return_value=None),
    ):
        for hit in page.results:
            message_helpers._message_to_response(hit, viewer_user_id=42)
    assert len(before_db.per_id_calls) == N_HITS
    assert before_db.batch_calls == []

    # AFTER: the route resolves the whole page in one batched query.
    after_db = _CountingEmbedsDB()
    response, fake_search = _run_route(after_db, page)
    assert len(response.results) == N_HITS
    assert len(fake_search.calls) == 1
    assert fake_search.calls[0]["user_id"] == 42  # authz scope unchanged
    assert len(after_db.batch_calls) == 1, "expected one batched suppression query"
    assert after_db.per_id_calls == [], "no per-message lookup may remain"


def test_batched_ids_are_exactly_the_hit_ids():
    """The batched query is bounded to this page's ids - no cross-user ids."""
    db = _CountingEmbedsDB()
    _run_route(db)

    sql, params = db.batch_calls[0]
    assert "embed_message_embeds" in sql
    assert "GROUP BY message_id" in sql
    assert set(params) == {FIRST_ID + i for i in range(N_HITS)}
    assert len(params) == N_HITS  # no duplicates / no extra ids


def test_suppression_result_parity():
    """Hits flagged suppressed by the DB are the only ones hidden from output."""
    db = _CountingEmbedsDB()
    response, _ = _run_route(db)

    for i, result in enumerate(response.results):
        mid = FIRST_ID + i
        assert int(result.id) == mid
        if mid in SUPPRESSED_IDS:
            assert result.metadata == {"embeds_suppressed": True}
            assert result.embeds == []
        else:
            assert result.metadata is None


def test_fail_closed_when_batch_query_unavailable():
    """A failed batch query falls back to per-id checks; suppression is kept."""
    db = _CountingEmbedsDB(fail_batch=True)
    response, _ = _run_route(db)

    assert len(db.batch_calls) == 1
    assert set(db.per_id_calls) == {FIRST_ID + i for i in range(N_HITS)}
    for i, result in enumerate(response.results):
        mid = FIRST_ID + i
        assert (result.metadata == {"embeds_suppressed": True}) is (
            mid in SUPPRESSED_IDS
        )
