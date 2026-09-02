"""
Sanity tests for the redesigned streaming reindex (W9-A).

Verifies the security redesign of reindex_all: bounded-page streaming with
no whole-table plaintext materialization, page-boundary cancellation, live
progress reporting, and unchanged incremental/deleted-purge semantics.
"""

import pytest

import src.core.search as search


@pytest.fixture
def paged_manager(modules, user_pool):
    """Search manager plus a batch-spy indexer wrapper."""
    user1 = user_pool.get_user()
    user2 = user_pool.get_user()
    dm = modules.messaging.create_dm(user1.id, user2.id)
    ids = []
    for i in range(10):
        msg = modules.messaging.send_message(user1.id, dm.id, f"stream page probe {i}")
        ids.append(msg.id)

    search_mgr = modules.search._get_manager()
    real_indexer = search_mgr._indexer

    class SpyIndexer:
        """Delegates to the real indexer, counting batch vs single calls."""

        def __init__(self):
            self.batch_calls = 0
            self.single_calls = 0
            self.max_batch = 0
            self._real = real_indexer

        def initialize(self):
            return True

        def close(self):
            pass

        def index_message(self, m):
            self.single_calls += 1
            return self._real.index_message(m)

        def index_messages_batch(self, batch):
            self.batch_calls += 1
            self.max_batch = max(self.max_batch, len(batch))
            return self._real.index_messages_batch(batch)

        def remove_message(self, mid):
            return self._real.remove_message(mid)

        def __getattr__(self, name):
            return getattr(self._real, name)

    spy = SpyIndexer()
    original = search_mgr._indexer
    search_mgr._indexer = spy
    yield search_mgr, spy, ids, user1.id
    search_mgr._indexer = original


@pytest.mark.search
class TestStreamingReindex:
    """W9-A: reindex streams in bounded pages, never bulk-materializing."""

    def test_reindex_all_stream_pages(self, paged_manager):
        mgr, spy, ids, _ = paged_manager

        indexed = mgr.reindex_all(force=True, page_size=4)
        assert indexed >= 10
        # Batched writes: one batch per page, each no larger than page_size.
        assert spy.batch_calls >= 2, (
            "expected paged batch indexing, got single bulk call"
        )
        assert spy.max_batch <= 4, f"batch of {spy.max_batch} exceeds page size"
        assert spy.single_calls == 0

        rows = {
            r["message_id"]: r
            for r in mgr._db.fetch_all("SELECT * FROM search_message_index")
        }
        for mid in ids:
            assert mid in rows, f"message {mid} missing from bookkeeping"

    def test_reindex_all_cancel_at_page_boundary(self, paged_manager):
        mgr, spy, ids, _ = paged_manager

        pages_seen = []

        def cancel_after_first_page():
            return len(pages_seen) >= 1

        def on_progress(indexed_so_far, pages_done):
            pages_seen.append((indexed_so_far, pages_done))

        from src.core.search.manager.message_indexing import ReindexCancelled

        with pytest.raises(ReindexCancelled):
            mgr.reindex_all(
                force=True,
                page_size=3,
                should_cancel=cancel_after_first_page,
                on_progress=on_progress,
            )
        assert len(pages_seen) == 1, "cancel must stop at the page boundary"

    def test_reindex_progress_reports_each_page(self, paged_manager):
        mgr, spy, ids, _ = paged_manager

        pages_seen = []
        indexed = mgr.reindex_all(
            force=True,
            page_size=4,
            on_progress=lambda n, p: pages_seen.append((n, p)),
        )
        assert indexed >= 10
        assert pages_seen, "no progress callbacks fired"
        assert pages_seen[-1][0] == indexed, "final progress must equal total"
        counts = [n for n, _ in pages_seen]
        assert counts == sorted(counts), "progress must be monotonic"

    def test_incremental_skips_unchanged(self, paged_manager):
        mgr, spy, ids, user_id = paged_manager

        first = mgr.reindex_all(force=True, page_size=4)
        assert first >= 10

        # Incremental run re-selects nothing when nothing changed.
        second = mgr.reindex_all(force=False, page_size=4)
        assert second == 0

    def test_deleted_purged_after_streaming_rebuild(self, paged_manager):
        mgr, spy, ids, _ = paged_manager

        victim = ids[0]
        mgr._db.execute("UPDATE msg_messages SET deleted = 1 WHERE id = ?", (victim,))
        indexed = mgr.reindex_all(force=True, page_size=4)
        assert indexed >= 1

        rows = {
            r["message_id"]
            for r in mgr._db.fetch_all("SELECT message_id FROM search_message_index")
        }
        assert victim not in rows, "deleted message resurrected by rebuild"

    def test_page_size_from_config_key(self, paged_manager):
        """search.reindex_page_size drives paging when page_size is omitted."""
        mgr, spy, ids, _ = paged_manager

        mgr._config["reindex_page_size"] = 5
        indexed = mgr.reindex_all(force=True)  # no explicit page_size
        assert indexed >= 10
        assert spy.batch_calls >= 2
        assert spy.max_batch <= 5, (
            f"config knob ignored: batch of {spy.max_batch} exceeds configured page size"
        )

    def test_page_size_fallback_and_clamping(self, paged_manager):
        """Missing knob falls back to the default; bad values clamp to >= 1."""
        from src.core.search.manager.message_indexing import DEFAULT_REINDEX_PAGE_SIZE

        mgr, spy, ids, _ = paged_manager

        mgr._config.pop("reindex_page_size", None)
        indexed = mgr.reindex_all(force=True)
        assert indexed >= 10
        assert spy.max_batch <= DEFAULT_REINDEX_PAGE_SIZE

        # Zero / negative are clamped to 1 rather than crashing the run.
        spy2_calls = []
        original_batch = spy.index_messages_batch

        def tracking_batch(batch):
            spy2_calls.append(len(batch))
            return original_batch(batch)

        spy.index_messages_batch = tracking_batch
        indexed = mgr.reindex_all(force=True, page_size=0)
        assert indexed >= 10
        assert spy2_calls and all(n == 1 for n in spy2_calls)
