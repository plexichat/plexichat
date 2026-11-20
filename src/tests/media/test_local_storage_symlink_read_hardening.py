"""Regression tests for LocalStorage read-path symlink hardening.

The write paths (``store``/``store_stream``) already reject symlink components
and open the final destination with ``O_NOFOLLOW``. The read/metadata/delete
paths, however, validated containment on the *resolved* path with ``safe_join``
and then operated on the *unresolved* path, so an in-base symlink could silently
redirect a read (or an existence/size/delete check) to a different object, and a
symlink swapped in between the check and the open (TOCTOU) could escape the base
directory entirely. These tests keep those paths fail-closed.
"""

from __future__ import annotations

import pytest

from src.core.media.exceptions import StorageError
from src.core.media.storage.local import LocalStorage


def _make_base(tmp_path):
    base = tmp_path / "media"
    base.mkdir()
    return base


@pytest.mark.parametrize("method", ["retrieve", "retrieve_stream"])
def test_read_refuses_in_base_symlink(tmp_path, method):
    """An in-base symlink must not redirect a read to its target object."""
    base = _make_base(tmp_path)
    victim = base / "victim.bin"
    victim.write_bytes(b"SECRET")
    link = base / "link.bin"
    link.symlink_to(victim)

    storage = LocalStorage(str(base))
    with pytest.raises(StorageError):
        if method == "retrieve":
            storage.retrieve("link.bin")
        else:
            storage.retrieve_stream("link.bin")

    assert victim.read_bytes() == b"SECRET"
    assert link.is_symlink()


@pytest.mark.parametrize("method", ["exists", "get_size", "get_metadata", "delete"])
def test_metadata_paths_refuse_symlinks(tmp_path, method):
    """Existence/size/metadata/delete checks must not follow a symlink.

    Use an in-base target: ``_full_path`` already rejects symlinks that resolve
    outside the base, but an in-base symlink currently slips through and lets
    the check observe/operate on a different object than the one named.
    """
    base = _make_base(tmp_path)
    victim = base / "victim.bin"
    victim.write_bytes(b"SECRET")
    link = base / "link.bin"
    link.symlink_to(victim)

    storage = LocalStorage(str(base))
    with pytest.raises(StorageError):
        getattr(storage, method)("link.bin")

    assert victim.read_bytes() == b"SECRET"
    assert link.is_symlink()


def test_read_symlink_planted_after_validation_is_refused(tmp_path, monkeypatch):
    """A final-component symlink swapped in after the check cannot hijack a read."""
    base = _make_base(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"SECRET")
    target = base / "obj.bin"
    target.write_bytes(b"public")

    storage = LocalStorage(str(base))
    original_check = storage._reject_symlink_components
    planted = {"done": False}

    def check_then_plant(path):
        original_check(path)
        if not planted["done"] and path == "obj.bin":
            planted["done"] = True
            target.unlink()
            target.symlink_to(outside)

    monkeypatch.setattr(storage, "_reject_symlink_components", check_then_plant)

    with pytest.raises(StorageError):
        storage.retrieve("obj.bin")

    assert planted["done"] is True
    assert outside.read_bytes() == b"SECRET"


def test_normal_read_roundtrip_still_works(tmp_path):
    """Hardening must not break ordinary reads, sizes, metadata and deletes."""
    base = _make_base(tmp_path)
    storage = LocalStorage(str(base))

    assert storage.store(b"payload", "a/b/obj.bin", "application/octet-stream")
    assert storage.retrieve("a/b/obj.bin") == b"payload"
    assert storage.exists("a/b/obj.bin") is True
    assert storage.get_size("a/b/obj.bin") == len(b"payload")
    meta = storage.get_metadata("a/b/obj.bin")
    assert meta["exists"] is True
    assert meta["size"] == len(b"payload")

    stream, size = storage.retrieve_stream("a/b/obj.bin")
    try:
        assert stream.read() == b"payload"
        assert size == len(b"payload")
    finally:
        stream.close()

    assert storage.delete("a/b/obj.bin") is True
    assert storage.exists("a/b/obj.bin") is False
