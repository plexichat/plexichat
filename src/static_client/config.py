"""
Typed configuration for the static client feature.

Values are loaded from the central ``config`` store (config.yaml) and
validated on read. Defaults live in :mod:`src.config_defaults`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import utils.config as _config


@dataclass(frozen=True)
class GitLabConfig:
    """GitLab Releases API connection settings."""

    project_id: Optional[int] = None
    api_url: str = "https://gitlab.plexichat.com/api/v4"
    private_token_env: str = "PLEXICHAT_GITLAB_TOKEN"
    verify_tls: bool = True
    request_timeout_seconds: int = 30

    def resolved_token(self) -> Optional[str]:
        """Return the configured private token from the environment, if any."""
        return os.environ.get(self.private_token_env) or None


@dataclass(frozen=True)
class CacheControlConfig:
    """Per-file-type Cache-Control header values."""

    hashed_assets: str = "public, max-age=31536000, immutable"
    html: str = "no-store, max-age=0"
    other: str = "public, max-age=300"


@dataclass(frozen=True)
class SecurityHeadersConfig:
    """Static response security headers (sent on every served response)."""

    x_content_type_options: str = "nosniff"
    x_frame_options: str = "SAMEORIGIN"
    referrer_policy: str = "strict-origin-when-cross-origin"
    permissions_policy: str = "geolocation=(), microphone=(self), camera=()"
    content_security_policy: str = (
        "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; media-src 'self' blob:; "
        "worker-src blob:; connect-src 'self' wss:; "
        "manifest-src 'self'; frame-ancestors 'none';"
    )


@dataclass(frozen=True)
class StaticRateLimitTier:
    """Per-tier rate-limit settings for static responses."""

    requests: int = 300
    window_seconds: float = 60.0
    burst: int = 60


@dataclass(frozen=True)
class StaticRateLimitConfig:
    """Two-tier static response rate limiting (HTML vs hashed assets)."""

    enabled: bool = True
    html: StaticRateLimitTier = field(default_factory=StaticRateLimitTier)
    assets: StaticRateLimitTier = field(
        default_factory=lambda: StaticRateLimitTier(requests=1200, burst=120)
    )


@dataclass(frozen=True)
class ConfigInjectionConfig:
    """Runtime config.js injection settings."""

    enabled: bool = True
    filename: str = "config.js"
    content: str = (
        'window.PLEXICHAT_CONFIG = {{ serverUrl: "{origin}", '
        'hideServerField: true, defaultTheme: "ocean", '
        'version: "{version}" }};'
    )


@dataclass(frozen=True)
class StaticClientConfig:
    """Top-level static client configuration."""

    enabled: bool = False
    serve: bool = True
    install_dir: str = ""
    source: str = "gitlab_release"
    version_pin: str = "latest"
    auto_update: bool = True
    auto_update_min_age_seconds: int = 3600
    auto_update_check_interval_seconds: int = 3600
    git_lab: GitLabConfig = field(default_factory=GitLabConfig)
    cache_control: CacheControlConfig = field(default_factory=CacheControlConfig)
    security_headers: SecurityHeadersConfig = field(
        default_factory=SecurityHeadersConfig
    )
    rate_limit: StaticRateLimitConfig = field(default_factory=StaticRateLimitConfig)
    max_zip_size_bytes: int = 104857600
    spa_routes: Dict[str, str] = field(default_factory=dict)
    log_downloads: bool = False
    config_injection: ConfigInjectionConfig = field(
        default_factory=ConfigInjectionConfig
    )
    invite_redirect: bool = True

    def install_path(self) -> Path:
        """Absolute install directory path."""
        p = Path(self.install_dir).expanduser()
        if not p.is_absolute():
            p = (Path.home() / ".plexichat" / "client").resolve()
        return p

    def sorted_spa_routes(self) -> List[Tuple[str, str]]:
        """SPA routes ordered longest-prefix-first."""
        return sorted(self.spa_routes.items(), key=lambda kv: -len(kv[0]))


def _coerce_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return default


def _coerce_str(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def get_static_client_config() -> StaticClientConfig:
    """Load :class:`StaticClientConfig` from the central config store.

    Missing keys fall back to defaults; unknown keys are ignored.
    """
    try:
        raw = _config.get("static_client", {}) or {}
    except RuntimeError:
        raw = {}

    if not isinstance(raw, dict):
        raw = {}

    home_dir = Path.home() / ".plexichat"
    default_install = str(home_dir / "client")

    git_raw = raw.get("git_lab", {}) if isinstance(raw.get("git_lab"), dict) else {}
    project_id = _coerce_int(git_raw.get("project_id"), 0)
    project_id_opt: Optional[int] = project_id if project_id > 0 else None

    cache_raw = raw.get("cache_control", {}) or {}
    sec_raw = raw.get("security_headers", {}) or {}
    rl_raw = raw.get("rate_limit", {}) or {}
    cfg_raw = raw.get("config_injection", {}) or {}

    html_rl = rl_raw.get("html", {}) if isinstance(rl_raw.get("html"), dict) else {}
    assets_rl = (
        rl_raw.get("assets", {}) if isinstance(rl_raw.get("assets"), dict) else {}
    )

    spa_raw = raw.get("spa_routes", {}) or {}
    spa_routes: Dict[str, str] = {}
    if isinstance(spa_raw, dict):
        for k, v in spa_raw.items():
            if isinstance(k, str) and isinstance(v, str):
                prefix = k if k.startswith("/") else f"/{k}"
                spa_routes[prefix] = v

    return StaticClientConfig(
        enabled=_coerce_bool(raw.get("enabled"), False),
        serve=_coerce_bool(raw.get("serve"), True),
        install_dir=_coerce_str(raw.get("install_dir"), default_install),
        source=_coerce_str(raw.get("source"), "gitlab_release"),
        version_pin=_coerce_str(raw.get("version_pin"), "latest"),
        auto_update=_coerce_bool(raw.get("auto_update"), True),
        auto_update_min_age_seconds=_coerce_int(
            raw.get("auto_update_min_age_seconds"), 3600
        ),
        auto_update_check_interval_seconds=_coerce_int(
            raw.get("auto_update_check_interval_seconds"), 3600
        ),
        git_lab=GitLabConfig(
            project_id=project_id_opt,
            api_url=_coerce_str(
                git_raw.get("api_url"), "https://gitlab.plexichat.com/api/v4"
            ),
            private_token_env=_coerce_str(
                git_raw.get("private_token_env"), "PLEXICHAT_GITLAB_TOKEN"
            ),
            verify_tls=_coerce_bool(git_raw.get("verify_tls"), True),
            request_timeout_seconds=_coerce_int(
                git_raw.get("request_timeout_seconds"), 30
            ),
        ),
        cache_control=CacheControlConfig(
            hashed_assets=_coerce_str(
                cache_raw.get("hashed_assets"),
                "public, max-age=31536000, immutable",
            ),
            html=_coerce_str(cache_raw.get("html"), "no-store, max-age=0"),
            other=_coerce_str(cache_raw.get("other"), "public, max-age=300"),
        ),
        security_headers=SecurityHeadersConfig(
            x_content_type_options=_coerce_str(
                sec_raw.get("x_content_type_options"), "nosniff"
            ),
            x_frame_options=_coerce_str(sec_raw.get("x_frame_options"), "SAMEORIGIN"),
            referrer_policy=_coerce_str(
                sec_raw.get("referrer_policy"), "strict-origin-when-cross-origin"
            ),
            permissions_policy=_coerce_str(
                sec_raw.get("permissions_policy"),
                "geolocation=(), microphone=(self), camera=()",
            ),
            content_security_policy=_coerce_str(
                sec_raw.get("content_security_policy"),
                (
                    "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
                    "style-src 'self' https://fonts.googleapis.com; "
                    "font-src https://fonts.gstatic.com; "
                    "img-src 'self' data: blob:; media-src 'self' blob:; "
                    "worker-src blob:; connect-src 'self' wss:; "
                    "manifest-src 'self'; frame-ancestors 'none';"
                ),
            ),
        ),
        rate_limit=StaticRateLimitConfig(
            enabled=_coerce_bool(rl_raw.get("enabled"), True),
            html=StaticRateLimitTier(
                requests=_coerce_int(html_rl.get("requests"), 300),
                window_seconds=float(html_rl.get("window_seconds", 60.0)),
                burst=_coerce_int(html_rl.get("burst"), 60),
            ),
            assets=StaticRateLimitTier(
                requests=_coerce_int(assets_rl.get("requests"), 1200),
                window_seconds=float(assets_rl.get("window_seconds", 60.0)),
                burst=_coerce_int(assets_rl.get("burst"), 120),
            ),
        ),
        max_zip_size_bytes=_coerce_int(raw.get("max_zip_size_bytes"), 104857600),
        spa_routes=spa_routes,
        log_downloads=_coerce_bool(raw.get("log_downloads"), False),
        config_injection=ConfigInjectionConfig(
            enabled=_coerce_bool(cfg_raw.get("enabled"), True),
            filename=_coerce_str(cfg_raw.get("filename"), "config.js"),
            content=_coerce_str(
                cfg_raw.get("content"),
                (
                    'window.PLEXICHAT_CONFIG = {{ serverUrl: "{origin}", '
                    'hideServerField: true, defaultTheme: "ocean", '
                    'version: "{version}" }};'
                ),
            ),
        ),
        invite_redirect=_coerce_bool(raw.get("invite_redirect"), True),
    )


__all__ = [
    "CacheControlConfig",
    "ConfigInjectionConfig",
    "GitLabConfig",
    "SecurityHeadersConfig",
    "StaticClientConfig",
    "StaticRateLimitConfig",
    "StaticRateLimitTier",
    "get_static_client_config",
]
