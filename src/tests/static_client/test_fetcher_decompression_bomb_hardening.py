"""Regression tests for zip-bomb hardening in the static-client extractor.

``_safe_extract_zip`` is the one place a downloaded archive is expanded onto
disk. A small crafted archive must be rejected from metadata *before* any
member is materialized; a mid-stream budget check that leaves partially
extracted files behind is not enough.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from src.static_client import fetcher
from src.static_client.fetcher import FetchError, _safe_extract_zip


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _extract(raw: bytes, target, **kwargs):
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        return _safe_extract_zip(zf, target, **kwargs)


def test_oversized_member_rejected_before_any_file_is_written(tmp_path):
    """A small first member plus an oversized one must leave nothing behind.

    Before the fix the first member was fully written and the oversized
    member's empty file was created before the mid-stream budget check fired.
    """
    raw = _make_zip({"ok.txt": b"A" * 64, "big.txt": b"A" * 200})

    with pytest.raises(FetchError, match="max_uncompressed_bytes"):
        _extract(raw, tmp_path, max_uncompressed_bytes=128)

    assert not list(tmp_path.rglob("*")), "no partial extraction may remain"


def test_cumulative_size_rejected_before_any_file_is_written(tmp_path):
    """Several individually-small members that exceed the total are refused."""
    raw = _make_zip({f"part-{i}.txt": b"A" * 40 for i in range(4)})

    with pytest.raises(FetchError, match="max_uncompressed_bytes"):
        _extract(raw, tmp_path, max_uncompressed_bytes=100)

    assert not list(tmp_path.rglob("*"))


def test_entry_count_is_bounded(tmp_path, monkeypatch):
    """An archive with too many tiny members is rejected up front."""
    monkeypatch.setattr(fetcher, "_MAX_ARCHIVE_ENTRIES", 2)
    raw = _make_zip({f"f{i}.txt": b"x" for i in range(5)})

    with pytest.raises(FetchError, match="too many entries"):
        _extract(raw, tmp_path)

    assert not list(tmp_path.rglob("*"))


def test_high_expansion_ratio_is_rejected(tmp_path, monkeypatch):
    """A tiny compressed payload that expands hugely is refused from metadata."""
    monkeypatch.setattr(fetcher, "_RATIO_MIN_OUTPUT_BYTES", 1024 * 1024)
    raw = _make_zip({"bomb.txt": b"A" * (4 * 1024 * 1024)})

    with pytest.raises(FetchError, match="expansion ratio"):
        _extract(raw, tmp_path)

    assert not list(tmp_path.rglob("*"))


def test_traversal_and_symlink_members_are_still_rejected(tmp_path):
    """Pre-existing zip-slip/symlink defences must survive the refactor."""
    traversal = _make_zip({"../escape.txt": b"pwnd"})
    with pytest.raises(FetchError):
        _extract(traversal, tmp_path)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "../../etc/passwd")
    with pytest.raises(FetchError):
        _extract(buf.getvalue(), tmp_path)

    assert not list(tmp_path.rglob("*"))
