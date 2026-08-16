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
import time

import pytest

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

    # The background scan runs in an executor; poll until it resolves.
    status = "pending"
    for _ in range(200):
        row = db.fetch_one(
            "SELECT scan_status FROM media_files WHERE id = ?",
            (int(result.file_id),),
        )
        if row and row["scan_status"] != "pending":
            status = row["scan_status"]
            break
        time.sleep(0.05)

    # With no scanner configured the file must at least leave PENDING (SKIPPED);
    # it must never remain stuck pending forever (which made it unservable).
    assert status != "pending", "large streamed upload was never scanned"
