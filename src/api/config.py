"""
API configuration - Settings for the REST API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, cast
import os

from src.core.config.snapshot import VersionedConfigSnapshot, file_version

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
# No manual path manipulation needed; import via standard paths.

try:
    import utils.config as config
except ImportError:
    config = None


def _config_state() -> tuple:
    """Cheap version token for the API config source.

    Includes the identity of the live loader and the mtime/size of its config
    file: a new ``config.setup()`` (common in tests and reload paths) or an
    on-disk edit both produce a new token, while an untouched config keeps the
    same token so the cached value is reused without another read.
    """
    if config is None:
        return (None, ())
    loader = getattr(config, "_config_instance", None)
    path = getattr(loader, "config_path", None)
    return (id(loader), file_version(path))


@dataclass
class APIConfig:
    """API configuration settings."""

    title: str = "Plexichat API"
    description: str = "REST API for Plexichat messaging platform"
    version: Optional[str] = None
    api_prefix: str = "/api/v1"
    debug: bool = False
    # SECURITY: Empty list = fail closed (no CORS allowed). Must be explicitly configured.
    cors_origins: List[str] = field(default_factory=list)
    cors_allow_credentials: bool = True
    cors_allow_methods: List[str] = field(
        default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    cors_allow_headers: List[str] = field(
        default_factory=lambda: [
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "Accept",
            "Origin",
            "Range",
            "X-API-Access-Token",
        ]
    )
    cors_expose_headers: List[str] = field(
        default_factory=lambda: [
            "Content-Range",
            "Accept-Ranges",
            "Content-Length",
            "ETag",
        ]
    )
    access_token_required: bool = False
    docs_url: Optional[str] = "/docs"
    redoc_url: Optional[str] = "/redoc"
    openapi_url: Optional[str] = "/openapi.json"
    # Maximum request body size in bytes (default 10MB for voice message uploads)
    max_request_body_size: int = 10 * 1024 * 1024  # 10MB
    # Cap on the AnyIO default thread limiter used by run_in_threadpool for
    # sync endpoint handlers (default 16, not anyio's oversubscribed 40).
    max_worker_threads: int = 16


def _build_api_config() -> APIConfig:
    """Read, validate and materialise an :class:`APIConfig` from the source.

    Only called by the versioned snapshot on a cache miss, so the relatively
    expensive validation and version lookup run once per source change rather
    than on a fixed timer.
    """
    if config is None:
        return APIConfig()
    try:
        api_conf = config.get("api", {})
    except RuntimeError:
        return APIConfig()

    import utils.version as version

    try:
        current_ver = version.current_string()
    except RuntimeError:
        # Fallback if version not setup (e.g. tests)
        current_ver = "0.0.0"

    # SECURITY: Validate CORS origins - reject wildcards unless explicitly allowed
    cors_origins = api_conf.get("cors_origins", [])
    # Fallback to safe explicit origins if the config omitted them entirely
    # (an empty/None list would otherwise fail closed and break the browser
    # client, which is served from a different origin than the API).
    if not cors_origins:
        cors_origins = [
            "https://plexichat.com",
            "https://app.plexichat.com",
            "https://api.plexichat.com",
            "https://www.plexichat.com",
        ]
    allow_wildcard = api_conf.get("allow_wildcard_cors", False)

    import utils.logger as logger

    logger.debug(f"Raw api_conf: {api_conf}")
    logger.debug(f"cors_origins from conf: {cors_origins}")
    logger.debug(f"allow_wildcard from conf: {allow_wildcard}")

    if (cors_origins == ["*"] or "*" in cors_origins) and not allow_wildcard:
        logger.error(
            "SECURITY ERROR: CORS wildcard '*' is not allowed. Configure explicit origins."
        )
        cors_origins = []  # Fail closed - no CORS allowed

    # SECURITY: Reject wildcard methods/headers
    cors_methods = api_conf.get(
        "cors_allow_methods", ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    if cors_methods == ["*"] or "*" in cors_methods:
        cors_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

    cors_headers = api_conf.get(
        "cors_allow_headers",
        [
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "Accept",
            "Origin",
            "Range",
            "X-API-Access-Token",
        ],
    )
    if cors_headers == ["*"] or "*" in cors_headers:
        cors_headers = [
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "Accept",
            "Origin",
            "Range",
            "X-API-Access-Token",
        ]

    cors_expose_headers = api_conf.get(
        "cors_expose_headers",
        ["Content-Range", "Accept-Ranges", "Content-Length", "ETag"],
    )

    result = APIConfig(
        title=api_conf.get("title", "Plexichat API"),
        description=api_conf.get(
            "description", "REST API for Plexichat messaging platform"
        ),
        version=current_ver,
        api_prefix=api_conf.get("api_prefix", "/api/v1"),
        debug=api_conf.get("debug", False),
        cors_origins=cors_origins,
        cors_allow_credentials=api_conf.get("cors_allow_credentials", True),
        cors_allow_methods=cors_methods,
        cors_allow_headers=cors_headers,
        cors_expose_headers=cors_expose_headers,
        access_token_required=api_conf.get("access_token_required", False),
        docs_url=api_conf.get("docs_url", "/docs"),
        redoc_url=api_conf.get("redoc_url", "/redoc"),
        openapi_url=api_conf.get("openapi_url", "/openapi.json"),
        max_request_body_size=api_conf.get("max_request_body_size", 10 * 1024 * 1024),
        max_worker_threads=api_conf.get("max_worker_threads", 16),
    )
    return result


# A single versioned snapshot replaces the old five-minute TTL cache.  The
# TTL paid the full read/validate cost on every expiry and hid on-disk edits
# for up to five minutes; the snapshot reuses the value until the source
# actually changes and observes edits on the next access.
_api_config_snapshot = VersionedConfigSnapshot(
    cast(Callable[[], Dict[str, Any]], _build_api_config), version_fn=_config_state
)


def get_api_config() -> APIConfig:
    """Return the current API config from the versioned snapshot."""
    return cast(APIConfig, _api_config_snapshot.snapshot())


def clear_api_config_cache() -> None:
    """Drop the cached API config so the next call re-reads from source.

    Useful for tests and admin config-reload operations.
    """
    _api_config_snapshot.invalidate()
