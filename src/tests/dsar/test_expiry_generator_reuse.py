"""Expiry cleanup reuses one export generator for the whole batch."""

from __future__ import annotations

import time

import pytest

pytestmark = pytest.mark.integration


def test_expiry_cleanup_builds_one_generator_for_batch(db, monkeypatch):
    from src.core.dsar import export_formats as ef
    from src.core.dsar import setup as dsar_setup
    from src.core.dsar.harvester import DSARHarvester
    from src.core.dsar.manager import DSARManager
    from src.utils.encryption import generate_snowflake_id

    class _AuditLogStub:
        def log_deletion_event(self, *args, **kwargs):
            return None

    dsar_setup(db)
    monkeypatch.setattr(DSARHarvester, "_sweep_orphaned_files", lambda self: None)
    monkeypatch.setattr(DSARManager, "_get_audit_log", lambda self: _AuditLogStub())

    base_init = ef.ExportFormatGenerator.__init__
    counts = {"init": 0}

    def counting_init(self, *args, **kwargs):
        counts["init"] += 1
        base_init(self, *args, **kwargs)

    monkeypatch.setattr(ef.ExportFormatGenerator, "__init__", counting_init)

    deleted: list = []
    monkeypatch.setattr(
        ef.ExportFormatGenerator,
        "delete",
        lambda self, path: deleted.append(path) or True,
    )

    harvester = DSARHarvester(db)
    now = int(time.time())
    n = 12
    ids = []
    for _ in range(n):
        rid = generate_snowflake_id()
        ids.append(rid)
        db.execute(
            "INSERT INTO dsar_requests "
            "(id, user_id, status, requested_at, format, expires_at, "
            " storage_backend, storage_path) "
            "VALUES (?, ?, 'ready', ?, 'json', ?, 'local', ?)",
            (rid, 1, now, now - 10, f"dsar/expired/dsar_{rid}.json"),
        )

    harvester._cleanup_expired()

    assert counts["init"] == 1, (
        f"expiry cleanup built {counts['init']} export generators for {n} "
        "expired exports; expected one reused generator"
    )
    assert len(deleted) == n, f"expected {n} scrub deletes, got {len(deleted)}"

    placeholders = ",".join("?" for _ in ids)
    rows = db.fetch_all(
        f"SELECT status FROM dsar_requests WHERE id IN ({placeholders})",
        tuple(ids),
    )
    assert all(row["status"] == "expired" for row in rows)
