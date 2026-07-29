"""
API configuration - Settings for the REST API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import os
import sys
import time

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
# No manual path manipulation needed; import via standard paths.

try:
    import utils.config as config
except ImportError:
    config = None

# Module-level cache for API config (read on every request by auth middleware)
_api_config_cache: Optional[APIConfig] = None
_api_config_cache_time: float = 0
_API_CONFIG_CACHE_TTL = 300  # 5 minutes


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


def get_api_config() -> APIConfig:
    """Load API configuration from config file (cached for 5 minutes)."""
    global _api_config_cache, _api_config_cache_time
    now = time.time()
    if (
        _api_config_cache is not None
        and (now - _api_config_cache_time) < _API_CONFIG_CACHE_TTL
    ):
        return _api_config_cache

    if config is None:
        result = APIConfig()
        _api_config_cache = result
        _api_config_cache_time = now
        return result
    try:
        api_conf = config.get("api", {})
    except RuntimeError:
        result = APIConfig()
        _api_config_cache = result
        _api_config_cache_time = now
        return result

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

    if "pytest" in sys.modules:
        print(f"[DEBUG] Raw api_conf: {api_conf}")
        print(f"[DEBUG] cors_origins from conf: {cors_origins}")
        print(f"[DEBUG] allow_wildcard from conf: {allow_wildcard}")

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
    )
    _api_config_cache = result
    _api_config_cache_time = now
    return result


def clear_api_config_cache() -> None:
    """Clear the cached API config so the next call re-reads from disk.

    Useful for tests and admin config-reload operations.
    """
    global _api_config_cache, _api_config_cache_time
    _api_config_cache = None
    _api_config_cache_time = 0
