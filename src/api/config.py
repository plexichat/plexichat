"""
API configuration - Settings for the REST API.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
for path in [project_root, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    import utils.config as config
except ImportError:
    config = None


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
    docs_url: Optional[str] = "/docs"
    redoc_url: Optional[str] = "/redoc"
    openapi_url: Optional[str] = "/openapi.json"


def get_api_config() -> APIConfig:
    """Load API configuration from config file."""
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

    return APIConfig(
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
        docs_url=api_conf.get("docs_url", "/docs"),
        redoc_url=api_conf.get("redoc_url", "/redoc"),
        openapi_url=api_conf.get("openapi_url", "/openapi.json"),
    )
