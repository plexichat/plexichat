"""Regression tests for LocalStorage symlink/TOCTOU write hardening.

The storage backend used to validate a symlink-resolved path with ``safe_join``
but then wrote to the original, unresolved path with a plain ``open(..., 'wb')``.
That let an in-base symlink redirect a write to a different object, and left a
check-then-open window in which a symlink planted at the destination could
steer the write outside the base directory.

These tests keep the guards in place and verify normal writes still work.
"""

from __future__ import annotations

import io

import pytest

from src.core.media.exceptions import StorageError
from src.core.media.storage.local import LocalStorage


def _make_base(tmp_path):
    base = tmp_path / "media"
    base.mkdir()
    return base


@pytest.mark.parametrize("method", ["store", "store_stream"])
def test_write_refuses_symlink_component_within_base(tmp_path, method):
    """An in-base symlink must not redirect a write to its target object."""
    base = _make_base(tmp_path)
    victim = base / "victim.bin"
    victim.write_bytes(b"original")
    link = base / "link.bin"
    link.symlink_to(victim)

    storage = LocalStorage(str(base))
    payload = b"OVERWRITE"
    with pytest.raises(StorageError):
        if method == "store":
            storage.store(payload, "link.bin", "application/octet-stream")
        else:
            storage.store_stream(
                io.BytesIO(payload),
                "link.bin",
                "application/octet-stream",
                len(payload),
            )

    assert victim.read_bytes() == b"original"
    assert link.is_symlink()


def test_open_for_write_refuses_final_component_symlink(tmp_path):
    """O_NOFOLLOW rejects a symlink at the destination itself (TOCTOU end)."""
    base = _make_base(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    link = base / "link.bin"
    link.symlink_to(outside)

    fd = None
    try:
        with pytest.raises(OSError):
            fd = LocalStorage._open_for_write(str(link))
    finally:
        if fd is not None:
            import os

            os.close(fd)

    assert outside.read_bytes() == b"secret"
    assert link.is_symlink()


def test_symlink_planted_after_validation_is_refused(tmp_path, monkeypatch):
    """A symlink swapped in after the containment check cannot hijack the write."""
    base = _make_base(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    dest = base / "obj.bin"
    dest.write_bytes(b"original")

    storage = LocalStorage(str(base))
    original_check = storage._reject_symlink_components
    planted = {"done": False}

    def check_then_plant(path):
        original_check(path)
        if not planted["done"] and path == "obj.bin":
            planted["done"] = True
            dest.unlink()
            dest.symlink_to(outside)

    monkeypatch.setattr(storage, "_reject_symlink_components", check_then_plant)

    with pytest.raises(StorageError):
        storage.store(b"OVERWRITE", "obj.bin", "application/octet-stream")

    assert planted["done"] is True
    assert outside.read_bytes() == b"secret"


def test_normal_write_roundtrip_still_works(tmp_path):
    """The hardening must not break ordinary writes, including overwrites."""
    base = _make_base(tmp_path)
    storage = LocalStorage(str(base))

    assert storage.store(b"first", "a/b/obj.bin", "application/octet-stream") == (
        "a/b/obj.bin"
    )
    assert storage.retrieve("a/b/obj.bin") == b"first"

    # Overwrite an existing regular file (O_TRUNC), not just create.
    assert storage.store(b"second-longer", "a/b/obj.bin", "application/octet-stream")
    assert storage.retrieve("a/b/obj.bin") == b"second-longer"

    assert (
        storage.store_stream(
            io.BytesIO(b"streamed"), "a/b/stream.bin", "application/octet-stream", 8
        )
        == "a/b/stream.bin"
    )
    assert storage.retrieve("a/b/stream.bin") == b"streamed"
