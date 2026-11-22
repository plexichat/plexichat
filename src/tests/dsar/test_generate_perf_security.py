"""Regressions for the admin DSAR generate hot path.

Pins three properties of ``POST /admin/data-export/{id}/generate``:

* **Identical output** — dropping the per-export thread pool must not change
  the collected payload (compared against the former parallel strategy).
* **Performance shape** — collection stays on the calling thread (no fresh
  worker connections per export) and the audit chain reads its last hash with
  a single bounded tail read instead of one syscall per character.
* **Security** — the export id is validated and bound to the fetched row,
  storage object names cannot carry traversal/header bytes, and an oversized
  category is capped and marked incomplete so the quota gate fails closed.
"""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

import pytest

from src.core.dsar.security import (
    InvalidExportInput,
    bound_export_data,
    safe_export_filename,
    validate_export_id,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def subject(db, auth_manager, pii_gen):
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"gps_{uuid.uuid4().hex[:10]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )


def _canonical(payload: dict) -> str:
    """Deterministic representation ignoring category ordering and time stamps."""
    clean = {k: v for k, v in payload.items() if k != "exported_at"}
    return json.dumps(clean, sort_keys=True, default=str)


def _parallel_collect_all(db, user_id, config):
    """The former strategy: a fresh 8-thread pool per export."""
    from src.core.dsar.collector import DataCollector

    collector = DataCollector(db, config=config)
    result = {
        "exported_at": 0,
        "user_id": user_id,
        "export_version": "1.0",
    }
    items = list(collector._collectors.items())
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        futures = {pool.submit(c.collect, user_id): name for name, c in items}
        for future in as_completed(futures):
            result[futures[future]] = future.result()
    # Apply the same post-processing the production collector applies.
    from src.core.dsar.config import legally_required_pii_fields
    from src.core.dsar.privacy import (
        redact_internal_fields,
        scrub_third_party_pii,
        verify_export_scope,
    )

    verify_export_scope(result, user_id)
    scrubbed = scrub_third_party_pii(
        result,
        legally_required=legally_required_pii_fields(config),
    )
    # Keep the reference pipeline in sync with the production collector's
    # final fail-closed gate for internal-only secret/path fields.
    return redact_internal_fields(scrubbed)


# ---------------------------------------------------------------------------
# identical output
# ---------------------------------------------------------------------------


def test_sequential_collection_matches_parallel(db, subject):
    """Removing the thread pool must produce the very same export payload."""
    from src.core.dsar.collector import DataCollector
    from src.core.dsar.config import get_dsar_config

    config = get_dsar_config()
    produced = DataCollector(db, config=config).collect_all(subject.id)
    reference = _parallel_collect_all(db, subject.id, config)

    assert _canonical(produced) == _canonical(reference)


# ---------------------------------------------------------------------------
# performance shape
# ---------------------------------------------------------------------------


def test_collect_all_runs_on_the_calling_thread(db, subject, monkeypatch):
    """No worker threads / fresh DB connections are created per export."""
    from src.core.dsar.collector import DataCollector

    caller = threading.get_ident()
    seen = set()
    collector = DataCollector(db)
    for domain in collector._collectors.values():
        original = domain.collect

        def wrapped(user_id, _original=original):
            seen.add(threading.get_ident())
            return _original(user_id)

        monkeypatch.setattr(domain, "collect", wrapped)

    collector.collect_all(subject.id)

    assert seen == {caller}, "collectors must reuse the warm caller connection"


def test_audit_last_hash_uses_one_bounded_tail_read(tmp_path):
    """The hash chain walks back with one read(), not one per character."""
    from src.core.dsar.audit_log import DSARLog

    path = tmp_path / "chain.jsonl"
    path.write_text(
        '{"checksum": "aaa", "prev_hash": "000"}\n'
        '{"checksum": "bbb", "prev_hash": "aaa"}\n',
        encoding="utf-8",
    )
    log = DSARLog(file_path=str(path))

    class _CountingHandle:
        def __init__(self, handle):
            self._handle = handle
            self.reads = 0

        def seek(self, *args):
            return self._handle.seek(*args)

        def tell(self):
            return self._handle.tell()

        def read(self, size=-1):
            self.reads += 1
            return self._handle.read(size)

    with open(path, encoding="utf-8") as handle:
        counted = _CountingHandle(handle)
        assert log._read_last_hash_from_handle(counted) == "bbb"
        assert counted.reads == 1


# ---------------------------------------------------------------------------
# security
# ---------------------------------------------------------------------------


def test_validate_export_id_is_bounded_and_fail_closed():
    assert validate_export_id("123") == 123
    assert validate_export_id(456) == 456
    for bad in (0, -1, 2**63, 2**64, "abc", None, True, 1.5, "", "  "):
        with pytest.raises(InvalidExportInput):
            validate_export_id(bad)


def test_safe_export_filename_blocks_injection():
    assert safe_export_filename(123, "json") == "dsar_123.json"
    assert safe_export_filename(123, "ZIP") == "dsar_123.zip"
    for bad_id in ("../etc/passwd", "1/../../x", "..", 0, -1, None, "1\x00"):
        with pytest.raises(InvalidExportInput):
            safe_export_filename(bad_id, "json")
    for bad_fmt in ("../../evil", "json/../../x", "exe", "", None, "js\x00on"):
        with pytest.raises(InvalidExportInput):
            safe_export_filename(123, bad_fmt)


def test_generate_manual_binds_row_to_requested_id():
    """A lookup returning a different row must never satisfy an export id."""
    from src.core.dsar.manager import DSARManager

    class _WrongRowDB:
        def fetch_one(self, query, params=()):
            return {
                "id": 999,  # not the requested 123
                "status": "approved",
                "user_id": 1,
                "format": "json",
            }

    with pytest.raises(ValueError, match="DSAR request 123 not found"):
        DSARManager(_WrongRowDB()).generate_manual(123, admin_id=1)


def test_bounded_export_fails_closed_on_oversized_category():
    """Truncation marks the payload incomplete so no artifact is written."""
    from src.core.dsar.privacy import ExportIncompleteError, assert_export_ready

    data = {
        "user_id": 1,
        "messages": {"rows": [{"id": i} for i in range(10)]},
    }
    assert bound_export_data(data, max_rows=3) is True
    assert len(data["messages"]["rows"]) == 3
    assert data["export_metadata"]["complete"] is False

    with pytest.raises(ExportIncompleteError):
        assert_export_ready(
            data,
            {
                "max_records_per_category": 100,
                "max_total_records": 100,
                "max_export_bytes": 10**9,
            },
        )


def test_bounded_export_leaves_normal_payload_untouched():
    data = {"user_id": 1, "messages": {"rows": [{"id": i} for i in range(3)]}}
    assert bound_export_data(data, max_rows=10) is False
    assert "export_metadata" not in data
    assert len(data["messages"]["rows"]) == 3
