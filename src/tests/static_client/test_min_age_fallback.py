"""
Tests for the static client min_age fallback logic.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch


os.environ.setdefault("PLEXICHAT_SYSTEM_KEY", "a" * 64)

from src.static_client.config import (  # noqa: E402
    CacheControlConfig,
    ConfigInjectionConfig,
    GitLabConfig,
    SecurityHeadersConfig,
    StaticClientConfig,
    StaticRateLimitConfig,
)
from src.static_client.fetcher import ReleaseAsset  # noqa: E402
from src.static_client.manager import (  # noqa: E402
    InstallResult,
    StaticClientManager,
)
from src.utils.common_utils.utils.version.core import parse_version  # noqa: E402


def _make_cfg(tmp_path: str, min_age: int) -> StaticClientConfig:
    return StaticClientConfig(
        enabled=True,
        serve=True,
        install_dir=tmp_path,
        source="gitlab_release",
        version_pin="latest",
        auto_update=True,
        auto_update_min_age_seconds=min_age,
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


def _make_asset(tag: str) -> tuple:
    return (
        parse_version(tag),
        ReleaseAsset(
            tag_name=tag,
            released_at="2026-06-07T00:00:00Z",
            zip_url=f"http://example.invalid/{tag}.zip",
            sha256_url="",
        ),
    )


def test_min_age_falls_back_to_older_release():
    """When the newest release is below min_age, the manager picks the
    next-newer release instead of giving up."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = _make_asset("a.1.0-60")
        stale = _make_asset("a.1.0-59")

        mgr = StaticClientManager(_make_cfg(tmp, min_age=3600))

        with patch.object(
            StaticClientManager, "_candidate_releases", return_value=[fresh, stale]
        ):
            with patch.object(
                StaticClientManager,
                "_should_fetch",
                side_effect=lambda a, v, n: v.build < 60,
            ) as should:
                with patch.object(
                    StaticClientManager,
                    "_fetch_and_install",
                    return_value=InstallResult(
                        installed_version="a.1.0-59", already_current=False
                    ),
                ) as fetch:
                    result = mgr.ensure_active()
                    assert fetch.call_count == 1
                    assert fetch.call_args[0][0].build == 59
                    assert result.installed_version == "a.1.0-59"
                    assert should.call_count == 2


def test_below_min_age_returns_error_when_all_too_new():
    """If every newer release fails min_age, return below_min_age without
    installing anything."""
    with tempfile.TemporaryDirectory() as tmp:
        fresh = _make_asset("a.1.0-60")
        fresher = _make_asset("a.1.0-59")

        mgr = StaticClientManager(_make_cfg(tmp, min_age=3600))

        with patch.object(
            StaticClientManager, "_candidate_releases", return_value=[fresh, fresher]
        ):
            with patch.object(StaticClientManager, "_should_fetch", return_value=False):
                with patch.object(StaticClientManager, "_fetch_and_install") as fetch:
                    result = mgr.ensure_active()
                    assert fetch.call_count == 0
                    assert result.error == "below_min_age"
                    assert result.already_current is True


def test_already_on_newest_returns_current():
    """If the installed version matches the newest release, return
    already_current without any fetch attempt."""
    with tempfile.TemporaryDirectory() as tmp:
        newer = _make_asset("a.1.0-60")

        mgr = StaticClientManager(_make_cfg(tmp, min_age=3600))

        with patch.object(
            StaticClientManager, "_candidate_releases", return_value=[newer]
        ):
            with patch.object(
                StaticClientManager, "current_version", return_value="a.1.0-60"
            ):
                with patch.object(StaticClientManager, "_fetch_and_install") as fetch:
                    result = mgr.ensure_active()
                    assert fetch.call_count == 0
                    assert result.already_current is True
                    assert result.error is None


def test_older_candidates_are_skipped():
    """Releases older than the installed version must be skipped so the
    manager does not downgrade."""
    with tempfile.TemporaryDirectory() as tmp:
        newer = _make_asset("a.1.0-60")
        older = _make_asset("a.1.0-58")

        mgr = StaticClientManager(_make_cfg(tmp, min_age=3600))

        with patch.object(
            StaticClientManager, "_candidate_releases", return_value=[newer, older]
        ):
            with patch.object(
                StaticClientManager, "current_version", return_value="a.1.0-59"
            ):
                with patch.object(
                    StaticClientManager,
                    "_should_fetch",
                    return_value=True,
                ) as should:
                    with patch.object(
                        StaticClientManager,
                        "_fetch_and_install",
                        return_value=InstallResult(
                            installed_version="a.1.0-60", already_current=False
                        ),
                    ) as fetch:
                        result = mgr.ensure_active()
                        assert fetch.call_count == 1
                        assert fetch.call_args[0][0].build == 60
                        assert should.call_count == 1
                        assert result.installed_version == "a.1.0-60"


def test_config_template_does_not_raise_keyerror():
    """The default config.js template must format without KeyError now
    that the literal braces are escaped."""
    from src.static_client.config import get_static_client_config

    cfg = get_static_client_config()
    out = cfg.config_injection.content.format(
        origin="https://plexichat.example", version="a.1.0-60"
    )
    assert "serverUrl" in out
    assert "https://plexichat.example" in out
    assert "a.1.0-60" in out
    assert "{origin}" not in out
    assert "{version}" not in out
