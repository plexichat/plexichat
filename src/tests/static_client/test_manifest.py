"""Tests for the static-client integrity manifest.

Regression coverage for the POSIX path bug where ``verify_manifest`` replaced
forward slashes with backslashes, making every nested file (``assets/*``,
``js/*``, ``.vite/*``) fail verification on Linux.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.static_client.manifest import (
    generate_manifest,
    write_manifest,
    verify_manifest,
)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _signing_key() -> bytes:
    return hashlib.sha256(b"test-key").digest()


def test_verify_nested_files_passes(tmp_path: Path) -> None:
    """Nested files (assets, js, .vite) must verify on POSIX."""
    _write(tmp_path / "index.html", b"<html></html>")
    _write(tmp_path / "assets" / "app-abc123.js", b"console.log(1)")
    _write(tmp_path / "js" / "suite-hash-capture.js", b"window.X = 1")
    _write(tmp_path / ".vite" / "manifest.json", b"{}")

    files = generate_manifest(tmp_path)
    # config.js and manifest files are excluded, but nested assets are kept.
    assert "assets/app-abc123.js" in files
    assert "js/suite-hash-capture.js" in files
    assert ".vite/manifest.json" in files

    write_manifest(tmp_path, files, _signing_key())
    assert verify_manifest(tmp_path, _signing_key()) is True


def test_verify_detects_tampered_nested_file(tmp_path: Path) -> None:
    """Tampering a nested asset must invalidate the manifest."""
    _write(tmp_path / "assets" / "app-abc123.js", b"original")
    files = generate_manifest(tmp_path)
    write_manifest(tmp_path, files, _signing_key())
    assert verify_manifest(tmp_path, _signing_key()) is True

    _write(tmp_path / "assets" / "app-abc123.js", b"tampered")
    assert verify_manifest(tmp_path, _signing_key()) is False


def test_verify_detects_missing_nested_file(tmp_path: Path) -> None:
    """A deleted nested asset must invalidate the manifest."""
    _write(tmp_path / "assets" / "app-abc123.js", b"original")
    files = generate_manifest(tmp_path)
    write_manifest(tmp_path, files, _signing_key())

    (tmp_path / "assets" / "app-abc123.js").unlink()
    assert verify_manifest(tmp_path, _signing_key()) is False


def test_verify_detects_hmac_mismatch(tmp_path: Path) -> None:
    """A manifest signed with a different key must fail verification."""
    _write(tmp_path / "index.html", b"<html></html>")
    files = generate_manifest(tmp_path)
    write_manifest(tmp_path, files, _signing_key())
    assert verify_manifest(tmp_path, hashlib.sha256(b"other").digest()) is False
