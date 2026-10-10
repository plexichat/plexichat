"""
Regression tests for M6: read-modify-write races on seq allocation and
server-retention upserts (src/core/artifacts/repository.py).

- ``test_concurrent_append_artifact_op_seq_unique`` hammers
  ``append_artifact_op`` from multiple threads against the same artifact.
  Pre-fix code (``SELECT MAX(seq)+1`` without a ``UNIQUE`` retry loop) loses
  ops or raises ``UNIQUE constraint failed`` under contention; fixed code
  must return a dense, duplicate-free ``1..N`` sequence with no lost ops.
- ``test_concurrent_set_server_retention_days_single_row`` races concurrent
  retention upserts; the ``UNIQUE(server_id)`` + UPDATE-first/INSERT-retry
  path must leave exactly one row whose value is one of the written values.

Both tests FAIL on the pre-fix implementation (duplicate seqs / lost ops /
raised integrity errors / duplicate retention rows).
"""

import threading

from src.core.artifacts.repository import (
    append_artifact_op,
    get_server_retention_days,
    list_artifact_ops,
    set_server_retention_days,
)


def test_concurrent_append_artifact_op_seq_unique(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1)

    n_threads = 8
    ops_per_thread = 15
    total = n_threads * ops_per_thread

    barrier = threading.Barrier(n_threads)
    returned_seqs = []
    errors = []
    lock = threading.Lock()

    def _worker(tid: int):
        try:
            barrier.wait(timeout=30)
            for i in range(ops_per_thread):
                seq = append_artifact_op(
                    db, artifact.id, "edit", tid, {"t": tid, "i": i}
                )
                with lock:
                    returned_seqs.append(seq)
        except Exception as e:  # pragma: no cover - surfaced below
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
        assert not t.is_alive(), "append worker thread hung"

    assert not errors, f"workers raised: {errors!r}"
    # No lost ops: every append returned a seq (None means a dropped op).
    assert len(returned_seqs) == total
    assert all(s is not None for s in returned_seqs), "lost ops (None seq)"
    # No duplicate seq: the allocation race would collide here.
    assert len(set(returned_seqs)) == total, (
        f"duplicate seq values: {sorted(returned_seqs)}"
    )
    assert sorted(returned_seqs) == list(range(1, total + 1))

    # The persisted log agrees: N rows, ordered, gap-free.
    ops = list_artifact_ops(db, artifact.id)
    assert len(ops) == total
    assert [o["seq"] for o in ops] == list(range(1, total + 1))


def test_concurrent_set_server_retention_days_single_row(db):
    server_id = 90601
    values = [10, 20, 30, 40, 50, 60, 70, 80]

    barrier = threading.Barrier(len(values))
    errors = []
    lock = threading.Lock()

    def _worker(days: int):
        try:
            barrier.wait(timeout=30)
            set_server_retention_days(db, server_id, days)
        except Exception as e:  # pragma: no cover - surfaced below
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=_worker, args=(v,)) for v in values]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "retention worker thread hung"

    assert not errors, f"workers raised: {errors!r}"

    rows = db.fetch_all(
        "SELECT retention_days FROM server_artifact_settings WHERE server_id = ?",
        (server_id,),
    )
    assert len(rows) == 1, f"expected exactly one retention row, got {len(rows)}"
    assert int(rows[0]["retention_days"]) in values
    assert get_server_retention_days(db, server_id) in values

    # Sequential overwrite still converges deterministically.
    set_server_retention_days(db, server_id, 33)
    assert get_server_retention_days(db, server_id) == 33
    set_server_retention_days(db, server_id, None)
    assert get_server_retention_days(db, server_id) is None
