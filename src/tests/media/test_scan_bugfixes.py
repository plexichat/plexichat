"""
Regression tests for media bug fixes.

- M4: streamed / large (temp-path) uploads must still be malware-scanned.
  Previously the background scan was only dispatched when the bytes were held
  in memory, so any upload larger than the in-memory threshold (and every
  chunked upload) was stored with ``scan_status = pending`` and never scanned,
  making it permanently unservable.
"""

from __future__ import annotations

import io

import pytest

from src.tests._conftest.waiting import wait_until

pytestmark = pytest.mark.integration

# A valid PNG starts with this magic header; the rest of the payload is padding.
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def test_large_streamed_upload_is_scanned_not_left_pending(media_manager, db):
    payload = PNG_HEADER + b"\x00" * (9 * 1024 * 1024)  # > 8 MiB in-memory cap
    result = media_manager.upload_stream(
        user_id=1,
        stream=io.BytesIO(payload),
        filename="big.png",
        content_type="image/png",
        size=len(payload),
    )
    assert result is not None

    # The background scan runs in an executor; wait for the row to leave
    # PENDING instead of polling on a fixed sleep budget.
    def _resolved_scan_status():
        row = db.fetch_one(
            "SELECT scan_status FROM media_files WHERE id = ?",
            (int(result.file_id),),
        )
        if row and row["scan_status"] != "pending":
            return row["scan_status"]
        return None

    status = wait_until(
        _resolved_scan_status,
        timeout=10.0,
        message="large streamed upload was never scanned",
    )

    # With no scanner configured the file must at least leave PENDING (SKIPPED);
    # it must never remain stuck pending forever (which made it unservable).
    assert status != "pending"
