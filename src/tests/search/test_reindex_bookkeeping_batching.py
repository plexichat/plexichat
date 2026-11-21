"""Reindex bookkeeping writes must be batched, not one upsert per message.

``MessageIndexingMixin._index_messages_batch_with_bookkeeping`` used to loop
over every message in a page and call ``db.upsert`` once each. With the default
page size of 200 that is 200 write statements (and 200 commits) per page -- a
full reindex of 100k messages issued 100k single-row writes. The fix builds one
batched upsert per page through ``Database.upsert_many``, collapsing the write
path to one statement/commit per page while keeping the same ON CONFLICT
semantics and cache invalidation.
"""

import pytest

pytestmark = [pytest.mark.search]


@pytest.fixture
def reindex_env(modules, user_pool):
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()
    dm = modules.messaging.create_dm(user1.id, user2.id)
    ids = []
    for i in range(12):
        msg = modules.messaging.send_message(user1.id, dm.id, f"batch probe {i}")
        ids.append(msg.id)
    mgr = modules.search._get_manager()
    return mgr, ids


def _spy(db, monkeypatch):
    calls = []
    real_execute = db.execute
    real_execute_many = db.execute_many

    def rec_execute(query, params=None, auto_commit=True):
        calls.append(("execute", query, params))
        return real_execute(query, params, auto_commit=auto_commit)

    def rec_execute_many(query, params_list, auto_commit=True):
        calls.append(("execute_many", query, params_list))
        return real_execute_many(query, params_list, auto_commit=auto_commit)

    monkeypatch.setattr(db, "execute", rec_execute)
    monkeypatch.setattr(db, "execute_many", rec_execute_many)
    return calls


def test_reindex_bookkeeping_is_one_batch_per_page(reindex_env, monkeypatch):
    """12 messages / page_size=5 -> 3 batched writes, not 12 single upserts."""
    mgr, ids = reindex_env
    calls = _spy(mgr._db, monkeypatch)

    indexed = mgr.reindex_all(force=True, page_size=5)
    assert indexed >= len(ids)

    bookkeeping = [
        c
        for c in calls
        if "search_message_index" in c[1] and c[1].lstrip().upper().startswith("INSERT")
    ]
    assert len(bookkeeping) == 3, (
        "bookkeeping writes must be one batch per page (3 pages); got "
        f"{len(bookkeeping)} statements: {[c[0] for c in bookkeeping]}"
    )
    assert all(c[0] == "execute_many" for c in bookkeeping), (
        "each page's bookkeeping must be a single execute_many, not per-row upserts"
    )
    assert sum(len(c[2]) for c in bookkeeping) == len(ids), (
        "every reindexed message must still get a bookkeeping row"
    )


def test_reindex_bookkeeping_rows_are_complete(reindex_env, monkeypatch):
    """Batching must not drop or corrupt any bookkeeping row."""
    mgr, ids = reindex_env
    _spy(mgr._db, monkeypatch)

    mgr.reindex_all(force=True, page_size=5)

    rows = {
        r["message_id"]: r
        for r in mgr._db.fetch_all("SELECT * FROM search_message_index")
    }
    for mid in ids:
        assert mid in rows, f"message {mid} missing from bookkeeping"
