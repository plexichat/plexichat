"""Bounded audit-write batching must be fast *and* complete.

The buffer turns N per-event ``INSERT``+autocommit audit writes into
``ceil(N/capacity)`` ``execute_many`` calls.  These tests guard both sides of
that trade: the query-count reduction and the fact that no accepted audit row
is ever dropped (including on exceptional exit).
"""

import pytest

from src.core.servers.handlers.audit_handler import AuditHandler
from src.core.servers.handlers.audit_write_buffer import AuditWriteBuffer
from src.core.servers.models import AuditLogAction


INSERT_SQL = "INSERT INTO srv_audit_log (id) VALUES (?)"


class _FakeDB:
    def __init__(self, fail_on_flush=False):
        self.batches = []
        self.fail_on_flush = fail_on_flush

    def execute_many(self, sql, rows):
        if self.fail_on_flush:
            raise RuntimeError("flush failed")
        self.batches.append(list(rows))


class _FakeManager:
    def __init__(self, db):
        self._db = db
        self._n = 0

    def _generate_id(self):
        self._n += 1
        return 10_000 + self._n

    def _get_timestamp(self):
        return 1_700_000_000_000


# ---------------------------------------------------------------------------
# Buffer mechanics (fake DB)
# ---------------------------------------------------------------------------


class TestAuditWriteBuffer:
    def test_capacity_is_bounded_and_flushes_are_bounded(self):
        db = _FakeDB()
        with AuditWriteBuffer(db, INSERT_SQL, capacity=3) as buf:
            for i in range(7):
                buf.add((i,))

        assert [len(batch) for batch in db.batches] == [3, 3, 1]
        assert buf.accepted_count == 7
        assert buf.flushed_count == 7
        assert buf.flush_call_count == 3
        assert buf.pending_count == 0

    def test_rows_keep_order_and_none_are_dropped(self):
        db = _FakeDB()
        buf = AuditWriteBuffer(db, INSERT_SQL, capacity=4)
        rows = [tuple([i]) for i in range(11)]
        with buf:
            buf.add_many(rows)

        written = [row for batch in db.batches for row in batch]
        assert written == rows

    def test_failed_flush_keeps_rows_pending_for_retry(self):
        db = _FakeDB(fail_on_flush=True)
        buf = AuditWriteBuffer(db, INSERT_SQL, capacity=2)
        with pytest.raises(RuntimeError, match="flush failed"):
            buf.add((1,))
            buf.add((2,))

        assert buf.pending_count == 2
        assert buf.flushed_count == 0
        db.fail_on_flush = False
        assert buf.flush() == 2
        assert buf.pending_count == 0

    def test_close_flushes_even_when_body_raises(self):
        db = _FakeDB()

        with pytest.raises(ValueError, match="body failed"):
            with AuditWriteBuffer(db, INSERT_SQL, capacity=64) as buf:
                buf.add((1,))
                buf.add((2,))
                raise ValueError("body failed")

        assert db.batches == [[(1,), (2,)]]

    @pytest.mark.parametrize("bad_capacity", [0, -1])
    def test_rejects_non_positive_capacity(self, bad_capacity):
        with pytest.raises(ValueError):
            AuditWriteBuffer(_FakeDB(), INSERT_SQL, capacity=bad_capacity)


# ---------------------------------------------------------------------------
# AuditHandler integration (real migrated DB)
# ---------------------------------------------------------------------------


def _handler(db):
    return AuditHandler(_FakeManager(db))


def _audit_kwargs(server_id, user_id, action=AuditLogAction.MEMBER_ROLE_ADD):
    return dict(
        server_id=server_id,
        user_id=user_id,
        action=action,
        target_type="member",
        target_id=user_id,
        changes={"role_id": 42},
    )


def _count_rows(db, server_id):
    # Use a raw cursor: fetch_one() has a short-lived result cache and would
    # otherwise return the pre-flush count on the second measurement.
    cursor = db.execute(
        "SELECT COUNT(*) AS c FROM srv_audit_log WHERE server_id = ? AND action = ?",
        (server_id, AuditLogAction.MEMBER_ROLE_ADD.value),
    )
    row = cursor.fetchone()
    cursor.close()
    return row["c"]


class TestAuditHandlerBuffered:
    def test_buffered_writes_are_complete_and_collapse_to_one_batch(
        self, db, fresh_server
    ):
        server_id = fresh_server["server"].id
        user_id = fresh_server["owner"].id
        handler = _handler(db)
        kwargs = _audit_kwargs(server_id, user_id)

        db.reset_request_metrics()
        with handler.buffered(capacity=256) as buf:
            for _ in range(40):
                handler.log_audit(**kwargs)
        queries = db.get_request_metrics()["query_count"]

        assert buf.accepted_count == 40
        assert buf.flush_call_count == 1
        assert queries == 1, (
            "40 buffered audit events should cost one execute_many, "
            f"got {queries} queries"
        )
        assert _count_rows(db, server_id) == 40

    def test_query_count_is_bounded_but_completeness_is_unchanged(
        self, db, fresh_server
    ):
        server_id = fresh_server["server"].id
        user_id = fresh_server["owner"].id
        handler = _handler(db)
        kwargs = _audit_kwargs(server_id, user_id)
        n = 40

        # "Before": one synchronous INSERT+autocommit per event.
        db.reset_request_metrics()
        for _ in range(n):
            handler.log_audit(**kwargs)
        unbuffered_queries = db.get_request_metrics()["query_count"]
        unbuffered_rows = _count_rows(db, server_id)

        # "After": same events, bounded buffer flushed on exit.
        db.reset_request_metrics()
        with handler.buffered(capacity=256):
            for _ in range(n):
                handler.log_audit(**kwargs)
        buffered_queries = db.get_request_metrics()["query_count"]
        buffered_rows = _count_rows(db, server_id)

        assert unbuffered_queries == n
        assert buffered_queries < unbuffered_queries
        assert unbuffered_rows == n
        # The buffered phase must add exactly as many auditable rows as the
        # unbuffered phase -- batching changes cost, not audit completeness.
        assert buffered_rows == unbuffered_rows + n

    def test_immediate_mode_is_unchanged_outside_buffer(
        self, db, fresh_server, monkeypatch
    ):
        server_id = fresh_server["server"].id
        user_id = fresh_server["owner"].id
        handler = _handler(db)
        calls = {"execute": 0, "execute_many": 0}
        orig_execute = db.execute
        orig_many = db.execute_many

        def counted_execute(query, params=None, **kw):
            calls["execute"] += 1
            return orig_execute(query, params, **kw)

        def counted_many(query, params_list, **kw):
            calls["execute_many"] += 1
            return orig_many(query, params_list, **kw)

        monkeypatch.setattr(db, "execute", counted_execute)
        monkeypatch.setattr(db, "execute_many", counted_many)

        handler.log_audit(**_audit_kwargs(server_id, user_id))
        assert calls == {"execute": 1, "execute_many": 0}
        assert _count_rows(db, server_id) == 1

    def test_nested_buffering_reuses_outer_buffer(self, db, fresh_server):
        server_id = fresh_server["server"].id
        user_id = fresh_server["owner"].id
        handler = _handler(db)
        kwargs = _audit_kwargs(server_id, user_id)

        with handler.buffered(capacity=256) as outer:
            with handler.buffered(capacity=256) as inner:
                assert inner is outer
                handler.log_audit(**kwargs)
            # No premature flush from the inner context.
            assert outer.pending_count == 1

        assert outer.flush_call_count == 1
        assert _count_rows(db, server_id) == 1

    def test_exception_in_bulk_loop_still_flushes_all_rows(self, db, fresh_server):
        server_id = fresh_server["server"].id
        user_id = fresh_server["owner"].id
        handler = _handler(db)
        kwargs = _audit_kwargs(server_id, user_id)

        with pytest.raises(RuntimeError, match="loop aborted"):
            with handler.buffered(capacity=256):
                for _ in range(5):
                    handler.log_audit(**kwargs)
                raise RuntimeError("loop aborted")

        assert _count_rows(db, server_id) == 5
