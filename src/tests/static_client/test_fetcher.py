"""Tests for static-client zip extraction hardening (decompression bombs)."""

from __future__ import annotations

import io
import zipfile

import pytest

from src.static_client.fetcher import (
    FetchError,
    _safe_extract_zip,
    install_zip_at,
    list_installed_versions,
    prune_old_versions,
)


def _zip_bytes(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_extract_within_budget(tmp_path):
    zf_bytes = _zip_bytes({"index.html": b"<html></html>", "assets/app.js": b"x" * 100})
    with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
        _safe_extract_zip(zf, tmp_path, max_uncompressed_bytes=1024 * 1024)
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "assets" / "app.js").is_file()


def test_extract_exceeding_budget_raises(tmp_path):
    """Decompressed bytes are counted, not the (small) compressed size."""
    zf_bytes = _zip_bytes({"big.txt": b"A" * 100})
    with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
        with pytest.raises(FetchError, match="max_uncompressed_bytes"):
            _safe_extract_zip(zf, tmp_path, max_uncompressed_bytes=10)


def test_install_zip_at_enforces_budget(tmp_path):
    zip_path = tmp_path / "dist.zip"
    zip_path.write_bytes(_zip_bytes({"big.txt": b"A" * 200}))
    with pytest.raises(FetchError, match="max_uncompressed_bytes"):
        install_zip_at(zip_path, tmp_path / "out", max_uncompressed_bytes=10)


def test_no_budget_is_unlimited(tmp_path):
    zf_bytes = _zip_bytes({"big.txt": b"A" * 1000})
    with zipfile.ZipFile(io.BytesIO(zf_bytes)) as zf:
        _safe_extract_zip(zf, tmp_path)
    assert (tmp_path / "big.txt").is_file()


def _install_version(install_dir, version):
    d = install_dir / version
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_bytes(b"x")
    return d


def test_prune_keeps_active_and_recent(tmp_path):
    for v in ["a.1.0-50", "a.1.0-51", "a.1.0-52", "a.1.0-53", "a.1.0-54", "a.1.0-55"]:
        _install_version(tmp_path, v)

    removed = prune_old_versions(tmp_path, keep=["a.1.0-55"], max_total=3)

    assert set(removed) == {"a.1.0-50", "a.1.0-51", "a.1.0-52"}
    remaining = {iv.version for iv in list_installed_versions(tmp_path)}
    assert remaining == {"a.1.0-53", "a.1.0-54", "a.1.0-55"}


def test_prune_honors_max_total(tmp_path):
    for v in ["a.1.0-50", "a.1.0-51", "a.1.0-52", "a.1.0-53", "a.1.0-54", "a.1.0-55"]:
        _install_version(tmp_path, v)

    removed = prune_old_versions(tmp_path, keep=["a.1.0-55"], max_total=5)

    assert removed == ["a.1.0-50"]
    remaining = {iv.version for iv in list_installed_versions(tmp_path)}
    assert remaining == {"a.1.0-51", "a.1.0-52", "a.1.0-53", "a.1.0-54", "a.1.0-55"}


def test_prune_never_deletes_keep(tmp_path):
    _install_version(tmp_path, "a.1.0-54")
    _install_version(tmp_path, "a.1.0-55")

    # ``keep`` takes priority even when it would exceed ``max_total``.
    removed = prune_old_versions(tmp_path, keep=["a.1.0-54"], max_total=1)

    assert set(removed) == {"a.1.0-55"}
    remaining = {iv.version for iv in list_installed_versions(tmp_path)}
    assert remaining == {"a.1.0-54"}
