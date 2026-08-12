"""Coalesced-write behavior for PlexiSum workbooks.

Cell operations are acknowledged durably (main-database operation log +
revision CAS) and applied to a live in-memory copy; the immutable blob is
only promoted when the flush window is exceeded. These tests verify the
revision/blob cadence, durability across a simulated restart, visibility of
unflushed edits, the interaction with full-file rewrites, and that conflict
and idempotent-retry semantics are preserved.
"""

import glob

import pytest

import src.core.suite.sum.manager as sum_manager_module
from src.core.suite.sum.manager import SumConflictError, SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _apply(manager, owner, workbook_id, sheet_id, operation_id, row, column, value):
    return manager.apply_operation(
        owner.id,
        workbook_id,
        int(sheet_id),
        operation_id,
        "set_cell",
        row,
        [{"row": row, "column": column, "value_type": "number", "value": value}],
    )


def test_cell_ops_are_coalesced_without_blob_rewrites(
    db, two_users, server_manager, tmp_path
):
    """Multiple cell edits do not re-upload the workbook blob per op."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Coalesced")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])

    for i in range(10):  # below SUM_FLUSH_MAX_OPS (25)
        _apply(manager, owner, workbook_id, sheet_id, f"op-{i}", i, 0, i)

    # No new immutable blob: the live session holds the edits.
    files = sorted(glob.glob(str(tmp_path / str(workbook_id) / "rev-*.plexisum")))
    assert len(files) == 1

    # Edits are visible to reads and to the realtime replay surface.
    viewport = manager.read_viewport(owner.id, workbook_id, int(sheet_id), 0, 20, 0, 2)
    values = {int(c["row"]): c["value"] for c in viewport["cells"] if c["column"] == 0}
    assert values == {i: i for i in range(10)}
    replayed = manager.replay_operations(owner.id, workbook_id, after_revision=0)
    assert len(replayed) == 10
    assert manager.get_workbook(owner.id, workbook_id)["current_revision"] == 10


def test_flush_promotes_blob_and_compacts_log(db, two_users, server_manager, tmp_path):
    """Crossing the op threshold writes one blob and compacts the log."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Flush")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])

    for i in range(sum_manager_module.SUM_FLUSH_MAX_OPS + 1):
        _apply(manager, owner, workbook_id, sheet_id, f"flush-{i}", i, 0, i)

    files = sorted(glob.glob(str(tmp_path / str(workbook_id) / "rev-*.plexisum")))
    assert len(files) == 2  # rev-0 + the single flushed snapshot

    row = db.fetch_one(
        "SELECT blob_revision, current_revision FROM sum_workbooks WHERE id = ?",
        (workbook_id,),
    )
    # The flush fired the moment the 25-op window filled, so the blob is at
    # revision 25 while the final operation (26) is still buffered.
    assert int(row["blob_revision"]) == sum_manager_module.SUM_FLUSH_MAX_OPS
    assert int(row["current_revision"]) == sum_manager_module.SUM_FLUSH_MAX_OPS + 1
    # Log rows up to the flushed revision are compacted away; only the
    # still-buffered final operation remains.
    remaining = db.fetch_all(
        "SELECT result_revision FROM sum_operations WHERE workbook_id = ?",
        (workbook_id,),
    )
    assert [int(r["result_revision"]) for r in remaining] == [
        sum_manager_module.SUM_FLUSH_MAX_OPS + 1
    ]


def test_restart_recovers_unflushed_ops_from_log(
    db, two_users, server_manager, tmp_path
):
    """A fresh process rebuilds the live copy from blob + durable log."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Restart")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])

    for i in range(5):  # unflushed
        manager.apply_operation(
            owner.id,
            workbook_id,
            int(sheet_id),
            f"rs-{i}",
            "set_cell",
            i,
            [{"row": i, "column": 1, "value_type": "text", "value": f"v{i}"}],
        )

    # Simulate a process restart: drop the in-memory session and blob cache.
    SumManager._drop_session(workbook_id)
    sum_manager_module._WORKBOOK_FILE_CACHE.drop_workbook(workbook_id)

    fresh = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    viewport = fresh.read_viewport(owner.id, workbook_id, int(sheet_id), 0, 10, 0, 3)
    values = {int(c["row"]): c["value"] for c in viewport["cells"] if c["column"] == 1}
    assert values == {i: f"v{i}" for i in range(5)}
    # The realtime replay surface also sees the recovered operations.
    replayed = fresh.replay_operations(owner.id, workbook_id, after_revision=0)
    assert len(replayed) == 5


def test_full_rewrite_preserves_unflushed_edits(
    db, two_users, server_manager, tmp_path
):
    """A structural rewrite (new sheet) keeps buffered cell edits intact."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Rewrite")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])

    manager.apply_operation(
        owner.id,
        workbook_id,
        int(sheet_id),
        "rw-cell",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "text", "value": "kept"}],
    )
    created = manager.create_sheet(owner.id, workbook_id, "Second")

    viewport = manager.read_viewport(owner.id, workbook_id, int(sheet_id), 0, 5, 0, 2)
    assert viewport["cells"][0]["value"] == "kept"
    sheets = manager.get_workbook(owner.id, workbook_id)["sheets"]
    assert any(s["name"] == "Second" for s in sheets)
    # The rewrite promoted a blob containing the buffered edit.
    files = sorted(glob.glob(str(tmp_path / str(workbook_id) / "rev-*.plexisum")))
    assert len(files) == 2


def test_solo_workbook_edits_coalesce_one_blob_per_flush_window(
    db, two_users, server_manager, tmp_path, monkeypatch
):
    """Solo (no other subscribers) workbook edits promote one blob per window.

    Regression guard for the removed "solo fast-path": for workbooks without
    websocket subscribers it rewrote the entire workbook blob on EVERY
    operation — one full file upload per keystroke — and skipped the durable
    operation log. Every edit must now flow through the coalesced
    live-session machinery: N edits in a flush window produce exactly ONE
    immutable blob promotion at the window's closing revision, never one per
    edit.
    """
    owner, _ = two_users
    create_sum_tables(db)
    # Pin the age-based window so the op-count threshold is the ONLY flush
    # source; the promoted-revision list is then exact and deterministic even
    # on very slow CI workers.
    monkeypatch.setattr(sum_manager_module, "SUM_FLUSH_MAX_MS", 10**6)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Solo Coalesced")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])

    # The workbook is genuinely solo: nobody else is subscribed to it.
    assert manager._is_solo(workbook_id) is True

    # Record every blob promotion after creation (the rev-0 blob is written by
    # create_workbook before this wrapper is installed). ``_save_file`` is the
    # single promotion chokepoint for both coalesced flushes and rewrites.
    promoted = []
    original_save_file = manager._save_file

    def recording_save_file(workbook_id, revision, raw_bytes):
        promoted.append(int(revision))
        return original_save_file(workbook_id, revision, raw_bytes)

    monkeypatch.setattr(manager, "_save_file", recording_save_file)

    total = sum_manager_module.SUM_FLUSH_MAX_OPS * 2  # two full flush windows

    # First window, one op short of the threshold: edits stay buffered in the
    # live session — no new blob is written (only the rev-0 creation blob).
    for i in range(sum_manager_module.SUM_FLUSH_MAX_OPS - 1):
        _apply(manager, owner, workbook_id, sheet_id, f"solo-{i}", i, 0, i)
    assert promoted == []
    files = sorted(glob.glob(str(tmp_path / str(workbook_id) / "rev-*.plexisum")))
    assert len(files) == 1

    # Cross both windows: the flush fires exactly at each window's closing
    # revision, promoting one blob per window for all 50 buffered edits.
    for i in range(sum_manager_module.SUM_FLUSH_MAX_OPS - 1, total):
        _apply(manager, owner, workbook_id, sheet_id, f"solo-{i}", i, 0, i)

    # Exactly ONE promotion per window, at its closing revision. A per-edit
    # rewrite path would have recorded every revision from 1 to 50 here.
    assert promoted == [
        sum_manager_module.SUM_FLUSH_MAX_OPS,
        sum_manager_module.SUM_FLUSH_MAX_OPS * 2,
    ]

    # Blob cadence on disk matches: rev-0 (creation) + one blob per window.
    files = sorted(glob.glob(str(tmp_path / str(workbook_id) / "rev-*.plexisum")))
    assert len(files) == 3

    # A fresh process rebuilds every edit from the stored blobs (nothing was
    # lost to coalescing).
    SumManager._drop_session(workbook_id)
    sum_manager_module._WORKBOOK_FILE_CACHE.drop_workbook(workbook_id)
    fresh = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    viewport = fresh.read_viewport(
        owner.id, workbook_id, int(sheet_id), 0, total + 2, 0, 2
    )
    values = {int(c["row"]): c["value"] for c in viewport["cells"] if c["column"] == 0}
    assert values == {i: i for i in range(total)}


def test_conflict_and_idempotent_retry_still_work(
    db, two_users, server_manager, tmp_path
):
    """Stale base revisions conflict; retries replay rather than error."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Retry")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])

    manager.apply_operation(
        owner.id,
        workbook_id,
        int(sheet_id),
        "retry-op",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "number", "value": 1}],
    )
    with pytest.raises(SumConflictError):
        manager.apply_operation(
            owner.id,
            workbook_id,
            int(sheet_id),
            "stale-op",
            "set_cell",
            0,
            [{"row": 5, "column": 5, "value_type": "number", "value": 9}],
        )
    # Dropping the live session (simulating a crash after the ack) must not
    # break the idempotent retry: the op is recovered from the durable log.
    SumManager._drop_session(workbook_id)
    result = manager.apply_operation(
        owner.id,
        workbook_id,
        int(sheet_id),
        "retry-op",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "number", "value": 1}],
    )
    assert result["replayed"] is True
    assert result["revision"] == 1


def test_set_format_is_preserved_across_materialization(
    db, two_users, server_manager, tmp_path
):
    """A format-only op (__preserve__) keeps the cell value on replay."""
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Format")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])

    manager.apply_operation(
        owner.id,
        workbook_id,
        int(sheet_id),
        "fmt-value",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "text", "value": "hello"}],
    )
    manager.apply_operation(
        owner.id,
        workbook_id,
        int(sheet_id),
        "fmt-style",
        "set_format",
        1,
        [{"row": 0, "column": 0, "style": {"bold": True}}],
    )

    # Force re-materialization from blob + log.
    SumManager._drop_session(workbook_id)
    viewport = manager.read_viewport(owner.id, workbook_id, int(sheet_id), 0, 5, 0, 2)
    cell = next(
        (c for c in viewport["cells"] if c["row"] == 0 and c["column"] == 0),
        None,
    )
    assert cell is not None
    assert cell["value"] == "hello"
    assert cell["style"] == {"bold": True}


def test_time_based_flush_fires_after_window(
    db, two_users, server_manager, tmp_path, monkeypatch
):
    """The age-based flush window promotes the blob far below the op threshold.

    ``_maybe_flush`` fires when either the op count (SUM_FLUSH_MAX_OPS) or the
    age of the oldest buffered op (SUM_FLUSH_MAX_MS) is exceeded. A zero-length
    window exercises the time-based path deterministically: every acknowledged
    op promotes a blob immediately, yet the durable log is compacted exactly up
    to the flushed revision so nothing is lost or double-applied.
    """
    owner, _ = two_users
    create_sum_tables(db)
    monkeypatch.setattr(sum_manager_module, "SUM_FLUSH_MAX_OPS", 10**9)
    monkeypatch.setattr(sum_manager_module, "SUM_FLUSH_MAX_MS", 0)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "Temporal")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])

    _apply(manager, owner, workbook_id, sheet_id, "t-1", 0, 0, 1)
    _apply(manager, owner, workbook_id, sheet_id, "t-2", 1, 0, 2)

    # rev-0 (creation) plus one promoted blob per time-based flush.
    files = sorted(glob.glob(str(tmp_path / str(workbook_id) / "rev-*.plexisum")))
    assert len(files) == 3
    row = db.fetch_one(
        "SELECT blob_revision, current_revision FROM sum_workbooks WHERE id = ?",
        (workbook_id,),
    )
    assert int(row["blob_revision"]) == 2
    assert int(row["current_revision"]) == 2
    # The log was compacted up to the flushed revision after every flush.
    remaining = db.fetch_all(
        "SELECT result_revision FROM sum_operations WHERE workbook_id = ?",
        (workbook_id,),
    )
    assert remaining == []
    # A crash after the flush window leaves nothing unflushed to recover.
    SumManager._drop_session(workbook_id)
    sum_manager_module._WORKBOOK_FILE_CACHE.drop_workbook(workbook_id)
    fresh = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    viewport = fresh.read_viewport(owner.id, workbook_id, int(sheet_id), 0, 5, 0, 2)
    values = {int(c["row"]): c["value"] for c in viewport["cells"] if c["column"] == 0}
    assert values == {0: 1, 1: 2}


def test_eviction_flushes_pending_sessions_without_data_loss(
    db, two_users, server_manager, tmp_path, monkeypatch
):
    """LRU eviction promotes buffered edits before dropping the live copy.

    More workbooks than SUM_SESSION_MAX_ENTRIES are edited so materializations
    overflow the in-memory cap; each overflow evicts the least-recently-used
    session, which must flush its pending ops to a blob first. A hard crash
    (all sessions dropped + file cache cleared) then rebuilds every workbook
    from blob + durable log with no data loss.
    """
    # Pin the age-based window far beyond the test duration so eviction is the
    # ONLY flush source; the flushed-workbook count is then exact and the test
    # is deterministic even on very slow CI workers.
    monkeypatch.setattr(sum_manager_module, "SUM_FLUSH_MAX_MS", 10**6)
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    count = sum_manager_module.SUM_SESSION_MAX_ENTRIES + 5
    workbook_ids = []
    for i in range(count):
        workbook = manager.create_workbook(owner.id, f"Evict {i}")
        sheet_id = workbook["sheets"][0]["id"]
        workbook_id = int(workbook["id"])
        workbook_ids.append((workbook_id, int(sheet_id)))
        # One op per fresh workbook: base revision 0, cell (0, 0) = i.
        _apply(manager, owner, workbook_id, sheet_id, f"e-{i}", 0, 0, i)

    # The session cap held: the overflow materializations evicted entries.
    assert (
        len(sum_manager_module._WORKBOOK_SESSIONS)
        <= sum_manager_module.SUM_SESSION_MAX_ENTRIES
    )
    # Every eviction flushed its victim's pending op (CAS succeeded on the
    # untouched blob_revision), promoting a blob at revision 1.
    flushed = 0
    for workbook_id, _ in workbook_ids:
        row = db.fetch_one(
            "SELECT blob_revision FROM sum_workbooks WHERE id = ?", (workbook_id,)
        )
        if int(row["blob_revision"]) >= 1:
            flushed += 1
    assert flushed == count - sum_manager_module.SUM_SESSION_MAX_ENTRIES

    # Hard crash: drop every live session and the file cache, then rebuild.
    for workbook_id, _ in workbook_ids:
        SumManager._drop_session(workbook_id)
    sum_manager_module._WORKBOOK_FILE_CACHE.clear()
    fresh = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    for i, (workbook_id, sheet_id) in enumerate(workbook_ids):
        viewport = fresh.read_viewport(
            owner.id, workbook_id, sheet_id, 0, count + 2, 0, 2
        )
        values = {
            int(c["row"]): c["value"] for c in viewport["cells"] if c["column"] == 0
        }
        # Workbook i stored value i at (0, 0); the edit must survive the crash.
        assert values.get(0) == i, f"workbook {workbook_id} lost its edit"


def test_multi_worker_flush_cas_prevents_clobbering(
    db, two_users, server_manager, tmp_path
):
    """A stale session's flush loses the blob_revision CAS and is discarded.

    Two processes editing the same workbook each hold a session materialized at
    the old blob revision. The first flush wins; the second must fail its
    ``WHERE blob_revision = ?`` gate, delete its orphaned blob, and leave the
    winning blob + durable log untouched so a fresh process rebuilds the full
    state.
    """
    owner, _ = two_users
    create_sum_tables(db)
    manager = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    workbook = manager.create_workbook(owner.id, "CAS")
    sheet_id = workbook["sheets"][0]["id"]
    workbook_id = int(workbook["id"])

    # Worker A fills the flush window; the blob is promoted at the threshold.
    for i in range(sum_manager_module.SUM_FLUSH_MAX_OPS):
        _apply(manager, owner, workbook_id, sheet_id, f"cas-a-{i}", i, 0, i)
    row = db.fetch_one(
        "SELECT blob_revision, current_revision FROM sum_workbooks WHERE id = ?",
        (workbook_id,),
    )
    assert int(row["blob_revision"]) == sum_manager_module.SUM_FLUSH_MAX_OPS
    assert int(row["current_revision"]) == sum_manager_module.SUM_FLUSH_MAX_OPS

    # Worker B still holds a session materialized at the OLD blob revision
    # (it had not yet observed A's promotion). Its flush must lose the CAS.
    live = sum_manager_module._WORKBOOK_SESSIONS[workbook_id]
    stale = sum_manager_module._LiveSession(live.connection, loaded_blob_revision=0)
    stale.pending = 1
    with pytest.raises(SumConflictError):
        manager._flush_session_unlocked(workbook_id, stale)

    # The losing flush deleted its orphaned blob; only creation + the winner's
    # promotion remain on disk and in metadata.
    files = sorted(glob.glob(str(tmp_path / str(workbook_id) / "rev-*.plexisum")))
    assert len(files) == 2
    row = db.fetch_one(
        "SELECT blob_revision, current_revision FROM sum_workbooks WHERE id = ?",
        (workbook_id,),
    )
    assert int(row["blob_revision"]) == sum_manager_module.SUM_FLUSH_MAX_OPS
    assert int(row["current_revision"]) == sum_manager_module.SUM_FLUSH_MAX_OPS

    # A fresh process materializes from the winning blob; nothing was lost.
    SumManager._drop_session(workbook_id)
    sum_manager_module._WORKBOOK_FILE_CACHE.drop_workbook(workbook_id)
    fresh = SumManager(db, servers_module=server_manager, storage_dir=str(tmp_path))
    viewport = fresh.read_viewport(owner.id, workbook_id, int(sheet_id), 0, 30, 0, 2)
    values = {int(c["row"]): c["value"] for c in viewport["cells"] if c["column"] == 0}
    assert values == {i: i for i in range(sum_manager_module.SUM_FLUSH_MAX_OPS)}
