"""
API configuration - Settings for the REST API.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Any
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
    title: str = "PlexiChat API"
    description: str = "REST API for PlexiChat messaging platform"
    version: str = "a.1.0-1"
    api_prefix: str = "/api/v1"
    debug: bool = False
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
    cors_allow_credentials: bool = True
    cors_allow_methods: List[str] = field(default_factory=lambda: ["*"])
    cors_allow_headers: List[str] = field(default_factory=lambda: ["*"])
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
    
    return APIConfig(
        title=api_conf.get("title", "PlexiChat API"),
        description=api_conf.get("description", "REST API for PlexiChat messaging platform"),
        version=api_conf.get("version", "a.1.0-1"),
        api_prefix=api_conf.get("api_prefix", "/api/v1"),
        debug=api_conf.get("debug", False),
        cors_origins=api_conf.get("cors_origins", ["*"]),
        cors_allow_credentials=api_conf.get("cors_allow_credentials", True),
        cors_allow_methods=api_conf.get("cors_allow_methods", ["*"]),
        cors_allow_headers=api_conf.get("cors_allow_headers", ["*"]),
        docs_url=api_conf.get("docs_url", "/docs"),
        redoc_url=api_conf.get("redoc_url", "/redoc"),
        openapi_url=api_conf.get("openapi_url", "/openapi.json"),
    )
