"""Regression tests for static-client integrity and path-confinement hardening.

* A signed manifest must detect files that exist on disk but were never in
  the signed file list — otherwise a planted file stays trusted and is served.
* The on-disk ``current_version`` marker must never be able to move the
  static middleware's filesystem root outside the install directory.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.static_client.config import (
    CacheControlConfig,
    ConfigInjectionConfig,
    GitLabConfig,
    SecurityHeadersConfig,
    StaticClientConfig,
    StaticRateLimitConfig,
)
from src.static_client.fetcher import ReleaseAsset, write_current_version
from src.static_client.manager import InstallResult, StaticClientManager
from src.static_client.manifest import (
    VERIFY_HASH_MISMATCH,
    VERIFY_OK,
    files_match_manifest,
    generate_manifest,
    verify_manifest_detailed,
    write_manifest,
)
from src.utils.common_utils.utils.version.core import parse_version


def _key() -> bytes:
    return hashlib.sha256(b"harden-test-key").digest()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make_cfg(install_dir: str, config_filename: str = "config.js"):
    return StaticClientConfig(
        enabled=True,
        serve=True,
        install_dir=install_dir,
        source="gitlab_release",
        version_pin="latest",
        auto_update=True,
        auto_update_min_age_seconds=0,
        auto_update_check_interval_seconds=60,
        git_lab=GitLabConfig(
            project_id=2,
            api_url="http://example.invalid/api/v4",
            private_token_env="PLEXICHAT_GITLAB_TOKEN",
            verify_tls=False,
            request_timeout_seconds=5,
        ),
        cache_control=CacheControlConfig(),
        security_headers=SecurityHeadersConfig(),
        rate_limit=StaticRateLimitConfig(),
        max_zip_size_bytes=1024,
        spa_routes={},
        log_downloads=False,
        config_injection=ConfigInjectionConfig(filename=config_filename),
        invite_redirect=True,
    )


def _make_asset(tag: str):
    return (
        parse_version(tag),
        ReleaseAsset(
            tag_name=tag,
            released_at="2026-06-07T00:00:00Z",
            zip_url=f"http://example.invalid/{tag}.zip",
            sha256_url="",
        ),
    )


def test_planted_unlisted_file_breaks_manifest_integrity(tmp_path: Path) -> None:
    """A file added after signing must invalidate the install."""
    _write(tmp_path / "index.html", b"<html>ok</html>")
    _write(tmp_path / "assets" / "app-abc.js", b"console.log(1)")
    key = _key()
    write_manifest(tmp_path, generate_manifest(tmp_path), key)
    assert verify_manifest_detailed(tmp_path, key) == VERIFY_OK

    # Attacker plants a new, unlisted asset. The signed file hashes are all
    # still correct, so an HMAC/hash-only check would wrongly call this OK.
    _write(tmp_path / "assets" / "injected.js", b"alert(document.domain)")

    assert verify_manifest_detailed(tmp_path, key) == VERIFY_HASH_MISMATCH
    assert files_match_manifest(tmp_path) is False


def test_planted_file_forces_quarantine_and_redownload() -> None:
    """A planted file must trigger the same repair path as a tampered file."""
    tag = "1.0.0a60"
    with tempfile.TemporaryDirectory() as tmp:
        mgr = StaticClientManager(_make_cfg(tmp))
        target = Path(tmp) / tag
        _write(target / "index.html", b"<html>ok</html>")
        key = _key()
        write_manifest(target, generate_manifest(target), key)
        write_current_version(Path(tmp), tag)
        assert verify_manifest_detailed(target, key) == VERIFY_OK

        # Plant an unlisted file in the trusted install.
        planted = target / "assets" / "evil.js"
        _write(planted, b"window.evil = 1")
        assert verify_manifest_detailed(target, key) != VERIFY_OK

        parsed, asset = _make_asset(tag)
        with patch("src.static_client.manager.get_signing_key", return_value=key):
            with patch.object(
                StaticClientManager,
                "_candidate_releases",
                return_value=[(parsed, asset)],
            ):
                with patch.object(
                    StaticClientManager,
                    "_fetch_and_install",
                    return_value=InstallResult(
                        installed_version=tag, already_current=False
                    ),
                ) as fetch:
                    mgr.ensure_active()
                    assert fetch.call_count == 1, "planted file must force re-download"

        assert not planted.exists(), "planted file must leave the live install path"
        assert list((Path(tmp) / ".quarantine").rglob("evil.js")), (
            "planted file must be quarantined, never left servable"
        )


def test_runtime_config_file_is_not_treated_as_planted(tmp_path: Path) -> None:
    """config.js (and a custom-named equivalent) must still be excluded."""
    _write(tmp_path / "index.html", b"<html>ok</html>")
    key = _key()
    write_manifest(tmp_path, generate_manifest(tmp_path), key)

    # Written after the manifest is signed, like the real runtime config.
    _write(tmp_path / "config.js", b"window.CONFIG={};")
    assert verify_manifest_detailed(tmp_path, key) == VERIFY_OK
    assert files_match_manifest(tmp_path) is True

    # A custom injection filename is excluded when passed explicitly.
    _write(tmp_path / "runtime-config.js", b"window.CONFIG={};")
    extra = {"runtime-config.js"}
    assert verify_manifest_detailed(tmp_path, key, extra_exclude=extra) == VERIFY_OK
    assert files_match_manifest(tmp_path, extra_exclude=extra) is True


def test_current_version_marker_cannot_escape_install_dir() -> None:
    """A tampered marker must not relocate the middleware's filesystem root."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        install_dir = root / "client"
        install_dir.mkdir()
        valid = install_dir / "1.0.0"
        _write(valid / "index.html", b"<html>trusted</html>")

        # A directory outside the install tree holding a file we must not serve.
        outside = root / "secret"
        _write(outside / "passwd", b"secret-data")
        outside_link = install_dir / "1.0.1"
        try:
            outside_link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            outside_link = None

        mgr = StaticClientManager(_make_cfg(str(install_dir)))

        # Valid marker resolves to the version directory inside install_dir.
        write_current_version(install_dir, "1.0.0")
        assert mgr.current_install_path() == valid.resolve()

        # Traversal, absolute paths, and non-version markers are all rejected.
        for marker in (
            "../secret",
            str(outside),
            "1.0.0/../secret",
            "../../etc",
            "not-a-version",
        ):
            write_current_version(install_dir, marker)
            assert mgr.current_install_path() is None, (
                f"marker {marker!r} must not become the static filesystem root"
            )

        # A valid-looking version that is a symlink to outside is rejected too.
        if outside_link is not None:
            write_current_version(install_dir, "1.0.1")
            assert mgr.current_install_path() is None
