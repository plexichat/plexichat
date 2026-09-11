"""M2 regression: manifest repair must NOT re-sign tampered files.

Tampering a single byte must leave the install UNTRUSTED: the tampered
file is quarantined/deleted, the manifest is NOT re-signed over tampered
bytes, and a re-download is forced.
"""

from __future__ import annotations

import hashlib
import logging
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
    VERIFY_OK,
    generate_manifest,
    verify_manifest_detailed,
    write_manifest,
)
from src.utils.common_utils.utils.version.core import parse_version


def _test_key() -> bytes:
    return hashlib.sha256(b"m2-test-key").digest()


def _make_cfg(tmp: str) -> StaticClientConfig:
    return StaticClientConfig(
        enabled=True,
        serve=True,
        install_dir=tmp,
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
        config_injection=ConfigInjectionConfig(),
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


def test_m2_tampered_file_is_quarantined_not_resigned(caplog):
    tag = "a.1.0-60"
    with tempfile.TemporaryDirectory() as tmp:
        mgr = StaticClientManager(_make_cfg(tmp))
        target = Path(tmp) / tag
        target.mkdir(parents=True, exist_ok=True)
        victim = target / "app.js"
        victim.write_bytes(b"console.log('original');")
        key = _test_key()
        write_manifest(target, generate_manifest(target), key)
        assert verify_manifest_detailed(target, key) == VERIFY_OK
        write_current_version(Path(tmp), tag)
        original_hmac = (target / "manifest.json.hmac").read_text(encoding="utf-8")

        # Tamper a single byte of the installed artifact.
        data = bytearray(victim.read_bytes())
        data[0] ^= 0xFF
        victim.write_bytes(bytes(data))
        assert verify_manifest_detailed(target, key) != VERIFY_OK

        parsed, asset = _make_asset(tag)
        caplog.set_level(logging.WARNING)
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
                    result = mgr.ensure_active()
                    # Re-download from the trusted source must be forced.
                    assert fetch.call_count == 1, (
                        "tampered install must force re-download, "
                        f"got {fetch.call_count} fetch calls"
                    )

        # A warning must be logged, without leaking secret material
        # (signing key or manifest HMAC) into the log record.
        assert "quarantin" in caplog.text.lower(), (
            "tampered install must log a quarantine warning"
        )
        assert key.hex() not in caplog.text, (
            "signing key must never appear in log output"
        )
        assert original_hmac not in caplog.text, (
            "manifest HMAC must never appear in log output"
        )

        # Tampered bytes must be quarantined/deleted, never left trusted.
        assert not victim.exists() or victim.read_bytes() != bytes(data), (
            "tampered file must be quarantined or deleted, not left in place"
        )
        quarantined = list((Path(tmp) / ".quarantine").rglob("app.js"))
        assert (not victim.exists()) or quarantined, (
            "tampered file must be quarantined (.quarantine/) or deleted"
        )
        # The manifest must NOT have been re-signed over tampered bytes:
        # no fresh manifest trusting the tampered content may remain at the
        # live install path.
        live_hmac_path = target / "manifest.json.hmac"
        if live_hmac_path.is_file():
            assert live_hmac_path.read_text(encoding="utf-8") == original_hmac, (
                "manifest was re-signed over tampered bytes"
            )
        if quarantined:
            q_hmac = quarantined[0].parent / "manifest.json.hmac"
            if q_hmac.is_file():
                assert q_hmac.read_text(encoding="utf-8") == original_hmac, (
                    "quarantined manifest was re-signed over tampered bytes"
                )
            assert (quarantined[0].parent / ".untrusted").is_file(), (
                "quarantined install must carry an .untrusted marker"
            )
        assert result is not None


def test_m2_tamper_fail_closed_when_redownload_fails():
    """Tampered bytes must stay quarantined even if re-download fails.

    Fail-closed: a failed re-download must not restore or re-trust the
    tampered install, and the manifest must not be re-signed.
    """
    tag = "a.1.0-60"
    with tempfile.TemporaryDirectory() as tmp:
        mgr = StaticClientManager(_make_cfg(tmp))
        target = Path(tmp) / tag
        target.mkdir(parents=True, exist_ok=True)
        victim = target / "app.js"
        victim.write_bytes(b"console.log('original');")
        key = _test_key()
        write_manifest(target, generate_manifest(target), key)
        assert verify_manifest_detailed(target, key) == VERIFY_OK
        write_current_version(Path(tmp), tag)
        original_hmac = (target / "manifest.json.hmac").read_text(encoding="utf-8")

        data = bytearray(victim.read_bytes())
        data[0] ^= 0xFF
        victim.write_bytes(bytes(data))
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
                        installed_version=None,
                        already_current=True,
                        error="download failed",
                    ),
                ) as fetch:
                    result = mgr.ensure_active()
                    assert fetch.call_count == 1
                    assert result.error == "download failed"

        # Tampered bytes must be gone from the live path even though the
        # re-download failed — never left servable, never re-signed.
        assert not victim.exists(), (
            "tampered file must stay quarantined when re-download fails"
        )
        assert list((Path(tmp) / ".quarantine").rglob("app.js")), (
            "tampered file must be quarantined (.quarantine/) on fetch failure"
        )
        live_hmac_path = target / "manifest.json.hmac"
        if live_hmac_path.is_file():
            assert live_hmac_path.read_text(encoding="utf-8") == original_hmac, (
                "manifest was re-signed over tampered bytes"
            )


def test_m2_valid_install_stays_trusted_no_redownload():
    tag = "a.1.0-60"
    with tempfile.TemporaryDirectory() as tmp:
        mgr = StaticClientManager(_make_cfg(tmp))
        target = Path(tmp) / tag
        target.mkdir(parents=True, exist_ok=True)
        (target / "app.js").write_bytes(b"console.log('ok');")
        key = _test_key()
        write_manifest(target, generate_manifest(target), key)
        write_current_version(Path(tmp), tag)

        parsed, asset = _make_asset(tag)
        with patch("src.static_client.manager.get_signing_key", return_value=key):
            with patch.object(
                StaticClientManager,
                "_candidate_releases",
                return_value=[(parsed, asset)],
            ):
                with patch.object(StaticClientManager, "_fetch_and_install") as fetch:
                    result = mgr.ensure_active()
                    assert fetch.call_count == 0
                    assert result.already_current is True
        assert (target / "app.js").is_file()
        assert not (Path(tmp) / ".quarantine").exists()
