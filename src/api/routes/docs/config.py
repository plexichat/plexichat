"""
Documentation configuration dataclasses.

This module contains all configuration dataclasses for the documentation system.
"""

import time
from dataclasses import dataclass, field, replace
from typing import List, Optional
from fastapi import Request


@dataclass
class ThemeConfig:
    """Theme configuration for documentation."""

    style: str = "dark"
    background_color: str = "#0d1117"
    surface_color: str = "#161b22"
    text_color: str = "#e6edf3"
    text_muted: str = "#8b949e"
    accent_color: str = "#58a6ff"
    accent_hover: str = "#79c0ff"
    border_color: str = "#21262d"
    border_light: str = "#30363d"
    font_family: str = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
    code_font: str = "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Consolas, 'Courier New', monospace"
    font_size_base: str = "15px"
    line_height: str = "1.7"
    border_radius_small: str = "4px"
    border_radius_medium: str = "6px"
    border_radius_large: str = "8px"
    transition_speed: str = "0.15s"
    sidebar_width: str = "260px"
    content_max_width: str = "860px"
    spacing_xs: str = "4px"
    spacing_sm: str = "8px"
    spacing_md: str = "16px"
    spacing_lg: str = "24px"
    spacing_xl: str = "32px"
    spacing_2xl: str = "48px"


@dataclass
class RateLimitConfig:
    """Rate limit configuration for docs."""

    enabled: bool = True
    requests: int = 60
    window_seconds: float = 60.0
    burst: int = 10
    per_ip: bool = True
    whitelist: List[str] = field(default_factory=list)


@dataclass
class CacheConfig:
    """Cache configuration."""

    enabled: bool = True
    ttl_seconds: int = 300
    cache_markdown: bool = True
    cache_html: bool = True
    max_entries: int = 100


@dataclass
class LoggingConfig:
    """Logging configuration."""

    enabled: bool = True
    level: str = "INFO"
    log_requests: bool = True
    log_errors: bool = True
    log_cache_hits: bool = False
    log_client_ip: bool = True


@dataclass
class SecurityConfig:
    """Security configuration."""

    allowed_extensions: List[str] = field(default_factory=lambda: [".md", ".json"])
    block_traversal: bool = True
    require_auth: bool = False


@dataclass
class NavItem:
    """Navigation item."""

    label: str
    path: str
    children: List["NavItem"] = field(default_factory=list)


@dataclass
class NavigationConfig:
    """Navigation configuration."""

    show_nav: bool = True
    items: List[NavItem] = field(default_factory=list)


@dataclass
class FeaturesConfig:
    """Feature flags."""

    enable_raw_endpoint: bool = True
    enable_search: bool = False
    show_version: bool = True
    show_last_updated: bool = True
    syntax_highlighting: bool = True


@dataclass
class DocsConfig:
    """Complete documentation configuration."""

    enabled: bool = True
    path: str = "/docs/api"
    title: str = "Plexichat Documentation"
    description: str = "Runtime documentation for the Plexichat backend"
    base_url: str = "https://your-plexichat-host.example/api/v1"
    websocket_url: str = "wss://your-plexichat-host.example/gateway"
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    navigation: NavigationConfig = field(default_factory=NavigationConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)


def _load_docs_config() -> DocsConfig:
    """Load documentation configuration from config file."""
    try:
        import utils.config as config

        docs_conf = config.get("docs", {})
    except RuntimeError:
        docs_conf = {}

    # Theme
    theme_conf = docs_conf.get("theme", {})
    theme = ThemeConfig(
        style=theme_conf.get("style", "dark"),
        background_color=theme_conf.get("background_color", "#0d1117"),
        surface_color=theme_conf.get("surface_color", "#161b22"),
        text_color=theme_conf.get("text_color", "#e6edf3"),
        text_muted=theme_conf.get("text_muted", "#8b949e"),
        accent_color=theme_conf.get("accent_color", "#58a6ff"),
        accent_hover=theme_conf.get("accent_hover", "#79c0ff"),
        border_color=theme_conf.get("border_color", "#21262d"),
        border_light=theme_conf.get("border_light", "#30363d"),
        font_family=theme_conf.get(
            "font_family",
            "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif",
        ),
        code_font=theme_conf.get(
            "code_font",
            "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Consolas, 'Courier New', monospace",
        ),
        font_size_base=theme_conf.get("font_size_base", "15px"),
        line_height=theme_conf.get("line_height", "1.7"),
        border_radius_small=theme_conf.get("border_radius_small", "4px"),
        border_radius_medium=theme_conf.get("border_radius_medium", "6px"),
        border_radius_large=theme_conf.get("border_radius_large", "8px"),
        transition_speed=theme_conf.get("transition_speed", "0.15s"),
        sidebar_width=theme_conf.get("sidebar_width", "260px"),
        content_max_width=theme_conf.get("content_max_width", "860px"),
        spacing_xs=theme_conf.get("spacing_xs", "4px"),
        spacing_sm=theme_conf.get("spacing_sm", "8px"),
        spacing_md=theme_conf.get("spacing_md", "16px"),
        spacing_lg=theme_conf.get("spacing_lg", "24px"),
        spacing_xl=theme_conf.get("spacing_xl", "32px"),
        spacing_2xl=theme_conf.get("spacing_2xl", "48px"),
    )

    # Rate limit
    rl_conf = docs_conf.get("rate_limit", {})
    rate_limit = RateLimitConfig(
        enabled=rl_conf.get("enabled", True),
        requests=rl_conf.get("requests", 60),
        window_seconds=rl_conf.get("window_seconds", 60.0),
        burst=rl_conf.get("burst", 10),
        per_ip=rl_conf.get("per_ip", True),
        whitelist=rl_conf.get("whitelist", []),
    )

    # Cache
    cache_conf = docs_conf.get("cache", {})
    cache = CacheConfig(
        enabled=cache_conf.get("enabled", True),
        ttl_seconds=cache_conf.get("ttl_seconds", 300),
        cache_markdown=cache_conf.get("cache_markdown", True),
        cache_html=cache_conf.get("cache_html", True),
        max_entries=cache_conf.get("max_entries", 100),
    )

    # Logging
    log_conf = docs_conf.get("logging", {})
    logging_config = LoggingConfig(
        enabled=log_conf.get("enabled", True),
        level=log_conf.get("level", "INFO"),
        log_requests=log_conf.get("log_requests", True),
        log_errors=log_conf.get("log_errors", True),
        log_cache_hits=log_conf.get("log_cache_hits", False),
        log_client_ip=log_conf.get("log_client_ip", True),
    )

    # Security
    sec_conf = docs_conf.get("security", {})
    security = SecurityConfig(
        allowed_extensions=sec_conf.get("allowed_extensions", [".md", ".json"]),
        block_traversal=sec_conf.get("block_traversal", True),
        require_auth=sec_conf.get("require_auth", False),
    )

    return DocsConfig(
        enabled=docs_conf.get("enabled", True),
        path=docs_conf.get("path", "/docs/api"),
        title=docs_conf.get("title", "Plexichat Documentation"),
        description=docs_conf.get(
            "description", "Runtime documentation for the Plexichat backend"
        ),
        base_url=docs_conf.get(
            "base_url", "https://your-plexichat-host.example/api/v1"
        ),
        websocket_url=docs_conf.get(
            "websocket_url", "wss://your-plexichat-host.example/gateway"
        ),
        theme=theme,
        rate_limit=rate_limit,
        cache=cache,
        logging=logging_config,
        security=security,
        features=FeaturesConfig(
            enable_raw_endpoint=docs_conf.get("features", {}).get(
                "enable_raw_endpoint", True
            ),
            enable_search=docs_conf.get("features", {}).get("enable_search", False),
            show_version=docs_conf.get("features", {}).get("show_version", True),
            show_last_updated=docs_conf.get("features", {}).get(
                "show_last_updated", True
            ),
            syntax_highlighting=docs_conf.get("features", {}).get(
                "syntax_highlighting", True
            ),
        ),
    )


# Cache the config but allow refresh
_config_cache: Optional[DocsConfig] = None
_config_cache_time: float = 0
_CONFIG_CACHE_TTL = 60  # Refresh config every 60 seconds


def get_docs_config() -> DocsConfig:
    """Get documentation configuration with caching."""
    global _config_cache, _config_cache_time
    now = time.time()
    if _config_cache is None or (now - _config_cache_time) > _CONFIG_CACHE_TTL:
        _config_cache = _load_docs_config()
        _config_cache_time = now
    return _config_cache


def is_docs_enabled() -> bool:
    """Check if documentation server is enabled."""
    return get_docs_config().enabled


def _runtime_docs_config(request: Request, conf: DocsConfig) -> DocsConfig:
    """Resolve runtime URLs from the current request host."""
    try:
        from src.api.config import get_api_config

        api_prefix = get_api_config().api_prefix
    except Exception:
        api_prefix = "/api/v1"

    host = request.headers.get("host", "localhost")
    scheme = request.url.scheme or "http"
    ws_scheme = "wss" if scheme == "https" else "ws"

    return replace(
        conf,
        base_url=f"{scheme}://{host}{api_prefix}",
        websocket_url=f"{ws_scheme}://{host}/gateway",
    )
