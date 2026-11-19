"""Regression: downloaded client code must be SHA256-verified before install.

The static client auto-updater installs ``dist.zip`` into the directory that is
served as the web app to every user.  A release with no ``.sha256`` asset used
to be installed with integrity checking silently disabled, turning a tampered
or compromised release into arbitrary JavaScript served to all clients.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.static_client.config import GitLabConfig
from src.static_client import fetcher
from src.static_client.fetcher import FetchError, ReleaseAsset


ZIP_BYTES = b"PK-fake-dist-payload-for-hash-checks"


def _asset(*, sha256_url: str = "") -> ReleaseAsset:
    return ReleaseAsset(
        tag_name="1.2.3",
        released_at="2026-01-01T00:00:00Z",
        zip_url="https://releases.example.com/dist.zip",
        sha256_url=sha256_url,
    )


def _fake_download(url: str, out_fp, **kwargs) -> int:
    """Write deterministic bytes depending on which asset is being fetched."""
    if url.endswith(".sha256"):
        payload = (hashlib.sha256(ZIP_BYTES).hexdigest() + "  dist.zip\n").encode()
    else:
        payload = ZIP_BYTES
    out_fp.write(payload)
    return len(payload)


def test_release_without_checksum_is_rejected(monkeypatch):
    """A missing checksum asset fails closed before the zip is even fetched."""

    def _forbid(*args, **kwargs):
        raise AssertionError("zip must not be downloaded when no checksum exists")

    monkeypatch.setattr(fetcher, "_download_to_stream", _forbid)
    with pytest.raises(FetchError, match="no SHA256 checksum asset"):
        fetcher.download_and_verify_release(
            _asset(), GitLabConfig(), max_bytes=1024 * 1024
        )


def test_release_without_checksum_allowed_when_explicitly_disabled(monkeypatch):
    monkeypatch.setattr(fetcher, "_download_to_stream", _fake_download)
    zip_path, actual = fetcher.download_and_verify_release(
        _asset(), GitLabConfig(require_sha256=False), max_bytes=1024 * 1024
    )
    assert Path(zip_path).read_bytes() == ZIP_BYTES
    assert actual == hashlib.sha256(ZIP_BYTES).hexdigest()


def test_malformed_checksum_is_rejected(monkeypatch):
    """A truncated/placeholder hash token cannot silently disable checking."""

    def _bad_sha(url, out_fp, **kwargs):
        out_fp.write(b"deadbeef\n")
        return len(b"deadbeef\n")

    monkeypatch.setattr(fetcher, "_download_to_stream", _bad_sha)
    with pytest.raises(FetchError, match="Malformed SHA256 checksum"):
        fetcher.download_and_verify_release(
            _asset(sha256_url="https://releases.example.com/dist.zip.sha256"),
            GitLabConfig(),
            max_bytes=1024 * 1024,
        )


def test_checksum_mismatch_still_rejected(monkeypatch):
    def _wrong_sha(url, out_fp, **kwargs):
        if url.endswith(".sha256"):
            out_fp.write(b"0" * 64 + b"\n")
            return 65
        out_fp.write(ZIP_BYTES)
        return len(ZIP_BYTES)

    monkeypatch.setattr(fetcher, "_download_to_stream", _wrong_sha)
    with pytest.raises(FetchError, match="SHA256 mismatch"):
        fetcher.download_and_verify_release(
            _asset(sha256_url="https://releases.example.com/dist.zip.sha256"),
            GitLabConfig(),
            max_bytes=1024 * 1024,
        )


def test_valid_checksum_install_verifies(monkeypatch):
    monkeypatch.setattr(fetcher, "_download_to_stream", _fake_download)
    zip_path, actual = fetcher.download_and_verify_release(
        _asset(sha256_url="https://releases.example.com/dist.zip.sha256"),
        GitLabConfig(),
        max_bytes=1024 * 1024,
    )
    assert actual == hashlib.sha256(ZIP_BYTES).hexdigest()
