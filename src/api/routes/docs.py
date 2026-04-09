"""
Documentation routes - Serve API documentation with dynamic rate limit info.

This module provides a configurable documentation server that:
- Serves markdown documentation as HTML with a modern sidebar layout
- Dynamically loads rate limits from actual config
- Has its own configurable rate limiting
- Supports caching, theming, and logging
"""

import re
import time
import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path
from dataclasses import dataclass, field, replace

from fastapi import APIRouter, HTTPException, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse

import utils.config as config

router = APIRouter(tags=["Documentation"])
DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs"

# Module state
_docs_cache: Dict[str, tuple[str, float]] = {}
_html_cache: Dict[str, tuple[str, float]] = {}


def _get_cached_value(
    cache: Dict[str, tuple[str, float]], key: str, ttl_seconds: int
) -> Optional[str]:
    """Return a cached value when present and still fresh."""
    entry = cache.get(key)
    if entry is None:
        return None

    value, cached_at = entry
    if (time.time() - cached_at) > ttl_seconds:
        cache.pop(key, None)
        return None

    return value


def _set_cached_value(
    cache: Dict[str, tuple[str, float]], key: str, value: str, max_entries: int
) -> None:
    """Store a cache value and keep the cache bounded."""
    cache[key] = (value, time.time())
    while len(cache) > max_entries:
        cache.pop(next(iter(cache)))


def _build_html_cache_key(
    source_key: str, title: str, current_path: str, conf: "DocsConfig"
) -> str:
    """Build a stable cache key for rendered HTML output."""
    return "|".join((source_key, title, current_path, repr(conf)))


def _decode_html_body(body: bytes | memoryview | str) -> str:
    """Decode a FastAPI/Starlette HTML body into text."""
    if isinstance(body, str):
        return body
    if isinstance(body, memoryview):
        return body.tobytes().decode("utf-8")
    return body.decode("utf-8")


@dataclass
class ThemeConfig:
    """Theme configuration for documentation."""

    style: str = "dark"
    background_color: str = "#0a0a0a"
    surface_color: str = "#141414"
    text_color: str = "#f5f5f5"
    text_muted: str = "#a0a0a0"
    accent_color: str = "#3b82f6"
    accent_hover: str = "#60a5fa"
    border_color: str = "#2a2a2a"
    border_light: str = "#333333"
    font_family: str = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"
    code_font: str = "'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace"
    font_size_base: str = "16px"
    line_height: str = "1.6"
    border_radius_small: str = "6px"
    border_radius_medium: str = "8px"
    border_radius_large: str = "12px"
    transition_speed: str = "0.2s"
    sidebar_width: str = "280px"
    content_max_width: str = "900px"
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
        docs_conf = config.get("docs", {})
    except RuntimeError:
        docs_conf = {}

    # Theme
    theme_conf = docs_conf.get("theme", {})
    theme = ThemeConfig(
        style=theme_conf.get("style", "dark"),
        background_color=theme_conf.get("background_color", "#0a0a0a"),
        surface_color=theme_conf.get("surface_color", "#141414"),
        text_color=theme_conf.get("text_color", "#f5f5f5"),
        text_muted=theme_conf.get("text_muted", "#a0a0a0"),
        accent_color=theme_conf.get("accent_color", "#3b82f6"),
        accent_hover=theme_conf.get("accent_hover", "#60a5fa"),
        border_color=theme_conf.get("border_color", "#2a2a2a"),
        border_light=theme_conf.get("border_light", "#333333"),
        font_family=theme_conf.get(
            "font_family",
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
        ),
        code_font=theme_conf.get(
            "code_font",
            "'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, 'Courier New', monospace",
        ),
        font_size_base=theme_conf.get("font_size_base", "16px"),
        line_height=theme_conf.get("line_height", "1.6"),
        border_radius_small=theme_conf.get("border_radius_small", "6px"),
        border_radius_medium=theme_conf.get("border_radius_medium", "8px"),
        border_radius_large=theme_conf.get("border_radius_large", "12px"),
        transition_speed=theme_conf.get("transition_speed", "0.2s"),
        sidebar_width=theme_conf.get("sidebar_width", "280px"),
        content_max_width=theme_conf.get("content_max_width", "900px"),
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


def clear_docs_cache() -> bool:
    """Clear documentation caches."""
    global _docs_cache, _html_cache, _config_cache, _config_cache_time
    _docs_cache.clear()
    _html_cache.clear()
    _config_cache = None
    _config_cache_time = 0
    return True


def get_docs_stats() -> Dict[str, Any]:
    """Get documentation server statistics."""
    return {
        "cache": {
            "docs_entries": len(_docs_cache),
            "html_entries": len(_html_cache),
        },
        "config": {
            "enabled": is_docs_enabled(),
            "path": get_docs_config().path,
        },
        "uptime": time.time() - _config_cache_time if _config_cache_time else 0,
    }


def get_api_rate_limits() -> Dict[str, Any]:
    """Get actual API rate limits from the rate limit configuration."""
    try:
        from src.core.ratelimit.config import (
            DEFAULT_ROUTE_LIMITS,
            get_bot_multiplier,
            get_global_limit,
            get_ip_limit,
            get_user_limit,
            get_webhook_multiplier,
            should_bypass_admin,
            should_bypass_internal,
        )

        global_limit = get_global_limit()
        user_limit = get_user_limit()
        ip_limit = get_ip_limit()

        limits = {
            "global": {
                "requests": global_limit.requests,
                "window_seconds": global_limit.window_seconds,
                "burst": global_limit.burst,
            },
            "user": {
                "requests": user_limit.requests,
                "window_seconds": user_limit.window_seconds,
                "burst": user_limit.burst,
            },
            "ip": {
                "requests": ip_limit.requests,
                "window_seconds": ip_limit.window_seconds,
                "burst": ip_limit.burst,
            },
            "bot_multiplier": get_bot_multiplier(),
            "webhook_multiplier": get_webhook_multiplier(),
            "admin_bypass": should_bypass_admin(),
            "internal_bypass": should_bypass_internal(),
            "routes": {},
        }

        for route, cfg in DEFAULT_ROUTE_LIMITS.items():
            limits["routes"][route] = {
                "requests": cfg.requests,
                "window_seconds": cfg.window_seconds,
                "burst": cfg.burst,
            }

        return limits
    except Exception:
        return {}


def _format_window_seconds(value: Any) -> str:
    """Format rate-limit window seconds for docs display."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:g}"


def _format_limit_summary(limit: Dict[str, Any]) -> str:
    """Format a human-readable rate limit summary."""
    window = _format_window_seconds(limit.get("window_seconds", 0))
    seconds_label = "second" if window == "1" else "seconds"
    return (
        f"{limit.get('requests', 0)} requests per {window} {seconds_label}, "
        f"burst {limit.get('burst', 0)}"
    )


def _build_rate_limit_defaults_rows(limits: Dict[str, Any]) -> str:
    """Build markdown table rows for global default rate limits."""
    rows = []
    labels = (("global", "global"), ("per-user", "user"), ("per-IP", "ip"))
    for label, key in labels:
        limit = limits.get(key)
        if not limit:
            continue
        rows.append(f"| {label} | {_format_limit_summary(limit)} |")
    return "\n".join(rows)


def _build_rate_limit_route_rows(limits: Dict[str, Any]) -> str:
    """Build markdown table rows for route-specific rate limits."""
    route_rows = []
    for route, cfg in sorted(limits.get("routes", {}).items()):
        route_rows.append(f"| `{route}` | {_format_limit_summary(cfg)} |")
    return "\n".join(route_rows)


def _build_rate_limit_policy_rows(limits: Dict[str, Any]) -> str:
    """Build markdown rows for rate-limit policy flags and multipliers."""

    def enabled(value: Any) -> str:
        return "enabled" if value else "disabled"

    return "\n".join(
        [
            f"| Bot multiplier | {limits.get('bot_multiplier', 1.0):g}x |",
            f"| Webhook multiplier | {limits.get('webhook_multiplier', 1.0):g}x |",
            f"| Admin bypass | {enabled(limits.get('admin_bypass', False))} |",
            f"| Internal bypass | {enabled(limits.get('internal_bypass', False))} |",
        ]
    )


def get_gateway_intents_docs_data() -> Dict[str, Any]:
    """Build dynamic docs data for gateway intents."""
    try:
        from src.api.websocket.intents import (
            ALL_INTENTS,
            DEFAULT_INTENTS,
            PRIVILEGED_INTENTS,
            get_intent_description,
        )
        from src.core.events.types import GatewayIntent

        rows = []
        for intent in GatewayIntent:
            value = int(intent)
            rows.append(
                {
                    "value": value,
                    "name": intent.name,
                    "default": bool(DEFAULT_INTENTS & value),
                    "privileged": bool(PRIVILEGED_INTENTS & value),
                    "description": get_intent_description(intent),
                }
            )

        return {
            "default_value": int(DEFAULT_INTENTS),
            "all_value": int(ALL_INTENTS),
            "privileged_value": int(PRIVILEGED_INTENTS),
            "rows": rows,
        }
    except Exception:
        return {
            "default_value": 0,
            "all_value": 0,
            "privileged_value": 0,
            "rows": [],
        }


def _build_gateway_intent_rows(data: Dict[str, Any]) -> str:
    """Build markdown rows for the gateway intents docs page."""
    rows = []
    for row in data.get("rows", []):
        rows.append(
            "| `{value}` | `{name}` | {default} | {privileged} | {description} |".format(
                value=row["value"],
                name=row["name"],
                default="Yes" if row.get("default") else "No",
                privileged="Yes" if row.get("privileged") else "No",
                description=row.get("description", "Unknown intent"),
            )
        )
    return "\n".join(rows)


def get_permissions_docs_data() -> Dict[str, Any]:
    """Build dynamic docs data for the permissions page."""
    try:
        from src.core.auth.permissions import (
            BOT_RESTRICTED_PERMISSIONS,
            DEFAULT_BOT_PERMISSIONS,
            DEFAULT_USER_PERMISSIONS,
            PERMISSIONS,
            get_permission_categories,
        )

        categories = get_permission_categories()
        category_rows = [
            {
                "name": category,
                "count": len(sorted(perms)),
                "permissions": sorted(perms),
            }
            for category, perms in sorted(categories.items())
        ]
        permission_rows = [
            {
                "name": name,
                "category": name.split(".", 1)[0],
                "description": description,
                "default_user": bool(DEFAULT_USER_PERMISSIONS.get(name, False)),
                "default_bot": bool(DEFAULT_BOT_PERMISSIONS.get(name, False)),
                "bot_restricted": name in BOT_RESTRICTED_PERMISSIONS,
            }
            for name, description in sorted(PERMISSIONS.items())
        ]

        return {
            "category_count": len(category_rows),
            "permission_count": len(permission_rows),
            "categories": category_rows,
            "permissions": permission_rows,
        }
    except Exception:
        return {
            "category_count": 0,
            "permission_count": 0,
            "categories": [],
            "permissions": [],
        }


def _build_permission_category_rows(data: Dict[str, Any]) -> str:
    """Build markdown rows for permission categories."""
    rows = []
    for category in data.get("categories", []):
        permission_list = ", ".join(
            f"`{perm}`" for perm in category.get("permissions", [])
        )
        rows.append(
            f"| `{category['name']}` | {category['count']} | {permission_list} |"
        )
    return "\n".join(rows)


def _build_permission_detail_rows(data: Dict[str, Any]) -> str:
    """Build markdown rows for individual permissions."""
    rows = []
    for permission in data.get("permissions", []):
        rows.append(
            "| `{name}` | `{category}` | {default_user} | {default_bot} | {bot_restricted} | {description} |".format(
                name=permission["name"],
                category=permission["category"],
                default_user="Yes" if permission.get("default_user") else "No",
                default_bot="Yes" if permission.get("default_bot") else "No",
                bot_restricted="Yes" if permission.get("bot_restricted") else "No",
                description=permission.get("description", ""),
            )
        )
    return "\n".join(rows)


def get_oauth_scopes_docs_data() -> Dict[str, Any]:
    """Build dynamic docs data for OAuth scopes."""
    try:
        from src.core.applications.oauth.scopes import (
            BOT_REQUIRED_SCOPES,
            PRIVILEGED_SCOPES,
            VALID_SCOPES,
            get_scope_description,
        )

        scopes = sorted(VALID_SCOPES)
        rows = [
            {
                "name": scope,
                "privileged": scope in PRIVILEGED_SCOPES,
                "bot_required": scope in BOT_REQUIRED_SCOPES,
                "description": get_scope_description(scope),
            }
            for scope in scopes
        ]

        return {
            "scope_count": len(rows),
            "privileged_count": sum(1 for row in rows if row["privileged"]),
            "bot_required_count": sum(1 for row in rows if row["bot_required"]),
            "rows": rows,
        }
    except Exception:
        return {
            "scope_count": 0,
            "privileged_count": 0,
            "bot_required_count": 0,
            "rows": [],
        }


def _build_oauth_scope_rows(data: Dict[str, Any]) -> str:
    """Build markdown rows for OAuth scope documentation."""
    rows = []
    for scope in data.get("rows", []):
        rows.append(
            "| `{name}` | {privileged} | {bot_required} | {description} |".format(
                name=scope["name"],
                privileged="Yes" if scope.get("privileged") else "No",
                bot_required="Yes" if scope.get("bot_required") else "No",
                description=scope.get("description", scope["name"]),
            )
        )
    return "\n".join(rows)


def get_app_config() -> Dict[str, Any]:
    """Get application configuration for documentation."""
    try:
        import utils.version as version

        return {
            "name": "Plexichat",
            "version": version.current_string(),
        }
    except Exception:
        return {"name": "Plexichat", "version": "unknown"}


def _doc_path(relative_path: str) -> Path:
    """Resolve a documentation file path relative to the backend docs root."""
    return DOCS_ROOT / relative_path


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


def _get_api_surface_paths() -> Dict[str, str]:
    """Return the public documentation surface paths."""
    try:
        from src.api.config import get_api_config

        api_conf = get_api_config()
        return {
            "docs_url": api_conf.docs_url or "/docs",
            "redoc_url": api_conf.redoc_url or "/redoc",
            "openapi_url": api_conf.openapi_url or "/openapi.json",
        }
    except Exception:
        return {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }


def _build_surface_nav_html(conf: DocsConfig, current_surface: str) -> str:
    """Build the shared top-level docs surface navigation."""
    api_paths = _get_api_surface_paths()
    items = [
        ("Narrative Docs", conf.path, current_surface == "portal"),
        ("OpenAPI Explorer", api_paths["docs_url"], current_surface == "swagger"),
        ("API Reference (ReDoc)", api_paths["redoc_url"], current_surface == "redoc"),
        ("Schema JSON", api_paths["openapi_url"], False),
    ]

    html = ['<nav class="surface-nav" aria-label="Documentation surfaces">']
    for label, href, active in items:
        if not href:
            continue
        active_class = "active" if active else ""
        html.append(f'<a href="{href}" class="surface-link {active_class}">{label}</a>')
    html.append("</nav>")
    return "".join(html)


def _build_runtime_pills_html(conf: DocsConfig) -> str:
    """Build runtime endpoint summary pills."""
    app_config = get_app_config()
    pills = [
        f'<span class="runtime-pill">REST {conf.base_url}</span>',
        f'<span class="runtime-pill">Gateway {conf.websocket_url}</span>',
        f'<span class="runtime-pill accent">Version {app_config["version"]}</span>',
    ]
    return f'<div class="runtime-pills">{"".join(pills)}</div>'


def _build_shell_header_html(
    conf: DocsConfig,
    current_surface: str,
    page_title: str,
    page_summary: str,
) -> str:
    """Build a branded shell header shared by all docs surfaces."""
    surface_labels = {
        "portal": "Narrative Docs",
        "swagger": "OpenAPI Explorer",
        "redoc": "API Reference",
    }
    surface_label = surface_labels.get(current_surface, "Documentation")
    return (
        '<header class="shell-header">'
        '<div class="shell-header-inner">'
        '<div class="shell-brand-block">'
        f'<a href="{conf.path}" class="brand-mark">PLEXI<span>CHAT</span></a>'
        f'<span class="surface-badge">{surface_label}</span>'
        f'<h1 class="shell-title">{page_title}</h1>'
        f'<p class="shell-summary">{page_summary}</p>'
        f"{_build_runtime_pills_html(conf)}"
        "</div>"
        f"{_build_surface_nav_html(conf, current_surface)}"
        "</div>"
        "</header>"
    )


def _build_brand_styles(conf: DocsConfig) -> str:
    """Build shared landing-inspired styles for docs surfaces."""
    theme = conf.theme
    return f"""
        :root {{
            --bg: {theme.background_color};
            --surface: {theme.surface_color};
            --text: {theme.text_color};
            --text-muted: {theme.text_muted};
            --accent: {theme.accent_color};
            --accent-hover: {theme.accent_hover};
            --border: {theme.border_color};
            --border-light: {theme.border_light};
            --font-main: {theme.font_family};
            --font-code: {theme.code_font};
            --font-size-base: {theme.font_size_base};
            --line-height: {theme.line_height};
            --border-radius-small: {theme.border_radius_small};
            --border-radius-medium: {theme.border_radius_medium};
            --border-radius-large: {theme.border_radius_large};
            --transition-speed: {theme.transition_speed};
            --sidebar-width: {theme.sidebar_width};
            --content-max-width: {theme.content_max_width};
            --spacing-xs: {theme.spacing_xs};
            --spacing-sm: {theme.spacing_sm};
            --spacing-md: {theme.spacing_md};
            --spacing-lg: {theme.spacing_lg};
            --spacing-xl: {theme.spacing_xl};
            --spacing-2xl: {theme.spacing_2xl};
        }}

        * {{ box-sizing: border-box; }}

        html {{ scroll-behavior: smooth; }}

        body {{
            margin: 0;
            color: var(--text);
            background: var(--bg);
            font-family: var(--font-main);
            font-size: var(--font-size-base);
            line-height: var(--line-height);
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}

        a:focus-visible,
        button:focus-visible,
        [role="button"]:focus-visible,
        input:focus-visible,
        select:focus-visible,
        textarea:focus-visible {{
            outline: 2px solid rgba(59, 130, 246, 0.7);
            outline-offset: 2px;
        }}

        .docs-layout {{
            display: grid;
            grid-template-columns: var(--sidebar-width) 1fr;
            min-height: 100vh;
        }}

        .sidebar {{
            background: var(--surface);
            border-right: 1px solid var(--border);
            height: 100vh;
            overflow-y: auto;
            padding: var(--spacing-xl) var(--spacing-lg);
            position: sticky;
            top: 0;
        }}

        .sidebar-header {{
            margin-bottom: var(--spacing-xl);
        }}

        .brand-mark {{
            color: var(--text);
            display: inline-block;
            font-size: 1.125rem;
            font-weight: 600;
            letter-spacing: 0.025em;
            text-decoration: none;
            margin-bottom: var(--spacing-sm);
        }}

        .brand-mark:hover {{
            color: var(--accent);
        }}

        .brand-mark span {{ color: var(--accent); }}

        .sidebar-caption {{
            color: var(--text-muted);
            display: block;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.1em;
            margin-bottom: var(--spacing-md);
            text-transform: uppercase;
        }}

        .sidebar-header h3 {{
            font-size: 1rem;
            font-weight: 600;
            margin: 0 0 var(--spacing-sm);
            color: var(--text);
        }}

        .sidebar-description {{
            color: var(--text-muted);
            font-size: 0.875rem;
            margin: 0;
            line-height: 1.5;
        }}

        .nav-category {{
            color: var(--text-muted);
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.1em;
            margin: var(--spacing-xl) var(--spacing-md) var(--spacing-sm);
            text-transform: uppercase;
        }}

        .nav-list {{ list-style: none; margin: 0; padding: 0; }}

        .nav-list li + li {{ margin-top: 2px; }}

        .nav-list a {{
            border: 1px solid transparent;
            border-radius: var(--border-radius-medium);
            color: var(--text-muted);
            display: block;
            font-size: 0.875rem;
            padding: var(--spacing-sm) var(--spacing-md);
            text-decoration: none;
            transition: all var(--transition-speed) ease;
        }}

        .nav-list a:hover,
        .nav-list a.active {{
            background: var(--surface);
            border-color: var(--border-light);
            color: var(--text);
        }}

        .nav-list a.active {{
            background: rgba(59, 130, 246, 0.12);
            border-color: rgba(59, 130, 246, 0.28);
            color: var(--text);
            position: relative;
        }}

        .nav-list a.active:before {{
            content: "";
            position: absolute;
            left: -1px;
            top: 8px;
            bottom: 8px;
            width: 2px;
            background: var(--accent);
            border-radius: 1px;
        }}

        .docs-main {{
            padding: 0;
            display: flex;
            justify-content: center;
        }}

        .page-card {{
            background: var(--bg);
            border-radius: 0;
            box-shadow: none;
            overflow: hidden;
            position: relative;
            width: 100%;
            max-width: var(--content-max-width);
            margin: 0 auto;
        }}

        .shell-header {{
            border-bottom: 1px solid var(--border);
        }}

        .shell-header-inner {{
            padding: var(--spacing-2xl) var(--spacing-xl) var(--spacing-lg);
        }}

        .shell-brand-block {{ margin-bottom: var(--spacing-lg); }}

        .surface-badge {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-large);
            color: var(--text-muted);
            display: inline-flex;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.05em;
            padding: var(--spacing-xs) var(--spacing-md);
            text-transform: uppercase;
            margin-bottom: var(--spacing-md);
        }}

        .shell-title {{
            font-size: 2.5rem;
            font-weight: 600;
            letter-spacing: -0.025em;
            line-height: 1.1;
            margin: 0 0 var(--spacing-md);
            color: var(--text);
        }}

        .shell-summary {{
            color: var(--text-muted);
            font-size: 1.125rem;
            margin: 0 0 var(--spacing-lg);
            line-height: 1.5;
        }}

        .surface-nav {{
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-sm);
        }}

        .surface-link {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            color: var(--text-muted);
            display: inline-flex;
            font-size: 0.875rem;
            font-weight: 500;
            padding: var(--spacing-sm) var(--spacing-md);
            text-decoration: none;
            transition: all var(--transition-speed) ease;
        }}

        .surface-link:hover,
        .surface-link.active {{
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }}

        .runtime-pills {{
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-sm);
        }}

        .runtime-pill {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-large);
            color: var(--text-muted);
            display: inline-flex;
            font-size: 0.75rem;
            font-weight: 500;
            padding: var(--spacing-xs) var(--spacing-sm);
        }}

        .runtime-pill.accent {{
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }}

        .content-container {{
            padding: var(--spacing-2xl) var(--spacing-xl);
        }}

        .content-container > :first-child {{ margin-top: 0; }}

        h1, h2, h3, h4, h5, h6 {{
            color: var(--text);
            font-weight: 600;
            line-height: 1.25;
            margin-top: var(--spacing-2xl);
            margin-bottom: var(--spacing-lg);
        }}

        h1 {{
            font-size: 2rem;
            letter-spacing: -0.025em;
            border-bottom: 1px solid var(--border);
            padding-bottom: var(--spacing-lg);
        }}

        h2 {{
            font-size: 1.5rem;
            letter-spacing: -0.025em;
        }}

        h3 {{
            font-size: 1.25rem;
        }}

        h4 {{
            font-size: 1rem;
        }}

        p, li, td, th {{ 
            color: var(--text-muted); 
            font-size: 1rem;
            line-height: 1.6;
        }}

        strong {{ 
            color: var(--text);
            font-weight: 600;
        }}

        a {{ 
            color: var(--accent); 
            text-decoration: none;
            transition: color var(--transition-speed) ease;
        }}

        a:hover {{ 
            color: var(--accent-hover);
        }}

        ul, ol {{ 
            margin: var(--spacing-lg) 0 var(--spacing-lg); 
            padding-left: var(--spacing-xl);
        }}

        li + li {{ margin-top: var(--spacing-sm); }}

        code {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-small);
            color: var(--text);
            font-family: var(--font-code);
            font-size: 0.875em;
            padding: 2px var(--spacing-xs);
        }}

        pre {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            overflow-x: auto;
            padding: var(--spacing-lg);
            margin: var(--spacing-lg) 0;
        }}

        pre code {{
            background: transparent;
            border: none;
            padding: 0;
            font-size: 0.875rem;
            line-height: 1.5;
        }}

        .code-block {{ 
            margin: var(--spacing-lg) 0; 
            position: relative;
        }}

        .copy-btn {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-small);
            color: var(--text-muted);
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            font-family: var(--font-main);
            font-size: 0.75rem;
            font-weight: 500;
            gap: var(--spacing-xs);
            padding: var(--spacing-xs) var(--spacing-sm);
            position: absolute;
            right: var(--spacing-md);
            top: var(--spacing-md);
            transition: all var(--transition-speed) ease;
        }}

        .copy-btn:hover {{ 
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }}

        .table-wrapper {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            margin: var(--spacing-lg) 0;
            overflow-x: auto;
        }}

        table {{ 
            border-collapse: collapse; 
            width: 100%;
        }}

        th, td {{
            border-bottom: 1px solid var(--border);
            padding: var(--spacing-md);
            text-align: left;
        }}

        th {{
            background: var(--bg);
            color: var(--text);
            font-size: 0.875rem;
            font-weight: 600;
            letter-spacing: 0.025em;
            text-transform: uppercase;
        }}

        tr:last-child td {{
            border-bottom: none;
        }}

        .note {{
            background: var(--surface);
            border: 1px solid var(--border-light);
            border-left: 4px solid var(--accent);
            border-radius: var(--border-radius-medium);
            margin: var(--spacing-lg) 0;
            padding: var(--spacing-md);
        }}

        .footer {{
            border-top: 1px solid var(--border);
            color: var(--text-muted);
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-md);
            margin-top: var(--spacing-2xl);
            padding: var(--spacing-lg) 0;
            font-size: 0.875rem;
        }}

        /* OpenAPI overrides */
        .plexi-openapi-page #swagger-ui,
        .plexi-openapi-page redoc {{
            display: block;
            padding: 0;
            position: relative;
            z-index: 1;
        }}

        .plexi-openapi-page .swagger-ui {{ color: var(--text); }}

        .plexi-openapi-page .swagger-ui .topbar {{ display: none; }}

        .plexi-openapi-page .swagger-ui .info,
        .plexi-openapi-page .swagger-ui .scheme-container,
        .plexi-openapi-page .swagger-ui .opblock,
        .plexi-openapi-page .swagger-ui .responses-wrapper,
        .plexi-openapi-page .swagger-ui .parameters-container,
        .plexi-openapi-page .swagger-ui .model-box {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            box-shadow: none;
        }}

        .plexi-openapi-page .swagger-ui .scheme-container {{
            margin: var(--spacing-lg) 0;
            padding: var(--spacing-md);
        }}

        .plexi-openapi-page .swagger-ui .info .title,
        .plexi-openapi-page .swagger-ui .info hgroup.main h2,
        .plexi-openapi-page .swagger-ui .info h1,
        .plexi-openapi-page .swagger-ui .opblock-tag {{
            color: var(--text);
            font-family: var(--font-main);
        }}

        .plexi-openapi-page .swagger-ui .info p,
        .plexi-openapi-page .swagger-ui .info li,
        .plexi-openapi-page .swagger-ui .markdown p,
        .plexi-openapi-page .swagger-ui .markdown li,
        .plexi-openapi-page .swagger-ui .response-col_description__inner p {{
            color: var(--text-muted);
        }}

        .plexi-openapi-page .swagger-ui .opblock {{
            overflow: hidden;
        }}

        .plexi-openapi-page .swagger-ui .opblock-summary {{
            align-items: center;
            border-color: var(--border);
        }}

        .plexi-openapi-page .swagger-ui .opblock.opblock-get {{
            background: var(--surface);
            border-color: var(--border);
            border-left: 3px solid rgba(34, 197, 94, 0.55);
        }}

        .plexi-openapi-page .swagger-ui .opblock.opblock-post,
        .plexi-openapi-page .swagger-ui .opblock.opblock-put,
        .plexi-openapi-page .swagger-ui .opblock.opblock-patch {{
            background: var(--surface);
            border-color: var(--border);
            border-left: 3px solid rgba(59, 130, 246, 0.55);
        }}

        .plexi-openapi-page .swagger-ui .opblock.opblock-delete {{
            background: var(--surface);
            border-color: var(--border);
            border-left: 3px solid rgba(239, 68, 68, 0.55);
        }}

        .plexi-openapi-page .swagger-ui .btn,
        .plexi-openapi-page .swagger-ui button,
        .plexi-openapi-page .swagger-ui select,
        .plexi-openapi-page .swagger-ui input,
        .plexi-openapi-page .swagger-ui textarea {{
            border-radius: var(--border-radius-small);
            font-family: var(--font-main);
        }}

        .plexi-openapi-page .swagger-ui input,
        .plexi-openapi-page .swagger-ui textarea,
        .plexi-openapi-page .swagger-ui select {{
            background: var(--surface);
            border: 1px solid var(--border);
            color: var(--text);
        }}

        .plexi-openapi-page .swagger-ui .btn.authorize,
        .plexi-openapi-page .swagger-ui .btn.execute,
        .plexi-openapi-page .swagger-ui .download-url-wrapper .select-label select {{
            border-color: var(--accent);
        }}
        
        .plexi-openapi-page .swagger-ui .btn.execute,
        .plexi-openapi-page .swagger-ui .btn.authorize {{
            background: var(--accent);
            color: white;
        }}

        .plexi-openapi-page .swagger-ui table tbody tr td,
        .plexi-openapi-page .swagger-ui table thead tr th,
        .plexi-openapi-page .swagger-ui .parameter__name,
        .plexi-openapi-page .swagger-ui .response-col_status {{
            color: var(--text);
        }}

        .plexi-openapi-page .swagger-ui .model,
        .plexi-openapi-page .swagger-ui .prop-type,
        .plexi-openapi-page .swagger-ui .tab li,
        .plexi-openapi-page .swagger-ui .parameter__type,
        .plexi-openapi-page .swagger-ui .parameter__deprecated,
        .plexi-openapi-page .swagger-ui .response-col_links {{
            color: var(--text-muted);
        }}

        .plexi-openapi-page .swagger-ui section.models {{
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            overflow: hidden;
        }}

        .plexi-openapi-page .swagger-ui section.models h4,
        .plexi-openapi-page .swagger-ui section.models h5 {{
            color: var(--text);
        }}

        .plexi-openapi-page .swagger-ui .model-toggle:after {{
            background: var(--accent);
        }}

        .plexi-openapi-page .menu-content,
        .plexi-openapi-page [role="search"] input,
        .plexi-openapi-page .api-content,
        .plexi-openapi-page .redoc-json,
        .plexi-openapi-page .redoc-markdown code,
        .plexi-openapi-page .redoc-markdown pre {{
            font-family: var(--font-main) !important;
        }}

        .plexi-openapi-page .menu-content {{
            background: var(--surface) !important;
            border-right: 1px solid var(--border) !important;
        }}

        .plexi-openapi-page .api-content {{
            background: transparent !important;
        }}

        .plexi-openapi-page .api-info h1,
        .plexi-openapi-page h1,
        .plexi-openapi-page h2,
        .plexi-openapi-page h3,
        .plexi-openapi-page h4,
        .plexi-openapi-page h5 {{
            color: var(--text) !important;
        }}

        .plexi-openapi-page .swagger-ui,
        .plexi-openapi-page .swagger-ui .markdown,
        .plexi-openapi-page .swagger-ui .renderedMarkdown,
        .plexi-openapi-page .swagger-ui .opblock-summary-description,
        .plexi-openapi-page .swagger-ui .response-col_description__inner,
        .plexi-openapi-page .swagger-ui .parameter__name,
        .plexi-openapi-page .swagger-ui .parameter__type,
        .plexi-openapi-page .swagger-ui .prop-type,
        .plexi-openapi-page .swagger-ui .model,
        .plexi-openapi-page .swagger-ui .model-title,
        .plexi-openapi-page .swagger-ui .tab li {{
            color: var(--text-muted) !important;
        }}

        .plexi-openapi-page .swagger-ui .opblock-summary-method,
        .plexi-openapi-page .swagger-ui .opblock-tag,
        .plexi-openapi-page .swagger-ui .info .title,
        .plexi-openapi-page .swagger-ui h1,
        .plexi-openapi-page .swagger-ui h2,
        .plexi-openapi-page .swagger-ui h3,
        .plexi-openapi-page .swagger-ui h4,
        .plexi-openapi-page .swagger-ui h5 {{
            color: var(--text) !important;
        }}

        .plexi-openapi-page [role="search"] input {{
            background: var(--surface) !important;
            border: 1px solid var(--border) !important;
            border-radius: var(--border-radius-medium) !important;
            box-shadow: none !important;
            color: var(--text) !important;
        }}

        .plexi-openapi-page code,
        .plexi-openapi-page pre,
        .plexi-openapi-page table {{
            border-color: var(--border) !important;
        }}

        .plexi-openapi-page pre,
        .plexi-openapi-page code {{
            background: var(--surface) !important;
            border-radius: var(--border-radius-small) !important;
        }}

        @media (max-width: 1024px) {{
            .docs-layout {{ grid-template-columns: 1fr; }}
            .sidebar {{
                border-right: 0;
                border-bottom: 1px solid var(--border);
                height: auto;
                position: relative;
                top: auto;
            }}
            .content-container {{
                padding: var(--spacing-xl) var(--spacing-lg);
            }}
        }}

        @media (max-width: 768px) {{
            .shell-header {{
                padding: var(--spacing-lg) var(--spacing-md);
            }}
            .content-container {{
                padding: var(--spacing-lg) var(--spacing-md);
            }}
            .shell-title {{
                font-size: 2rem;
            }}
            .surface-link {{ 
                width: 100%; 
                justify-content: center; 
            }}
        }}
    """


def render_swagger_ui_page(
    request: Request,
    title: str,
    openapi_url: str,
    oauth2_redirect_url: Optional[str] = None,
) -> HTMLResponse:
    """Render a branded Swagger UI page."""
    conf = _runtime_docs_config(request, get_docs_config())
    response = get_swagger_ui_html(
        openapi_url=openapi_url,
        title=f"{title} - Plexichat API Explorer",
        oauth2_redirect_url=oauth2_redirect_url,
        swagger_ui_parameters={
            "deepLinking": True,
            "displayRequestDuration": True,
            "docExpansion": "none",
            "defaultModelsExpandDepth": -1,
            "filter": True,
            "persistAuthorization": True,
            "syntaxHighlight": {"theme": "obsidian"},
        },
    )
    html = _decode_html_body(response.body)
    shell_header = _build_shell_header_html(
        conf,
        "swagger",
        "Plexichat API Explorer",
        "Interactive request explorer powered by the live OpenAPI schema.",
    )
    html = html.replace(
        "<body>", '<body class="plexi-openapi-page plexi-swagger-page">'
    )
    html = html.replace(
        '<div id="swagger-ui">',
        f'{shell_header}<div id="swagger-ui">',
    )
    html = html.replace("</head>", f"<style>{_build_brand_styles(conf)}</style></head>")
    return HTMLResponse(html)


def render_redoc_page(request: Request, title: str, openapi_url: str) -> HTMLResponse:
    """Render a branded ReDoc page."""
    conf = _runtime_docs_config(request, get_docs_config())
    response = get_redoc_html(
        openapi_url=openapi_url,
        title=f"{title} - Plexichat API Reference",
        with_google_fonts=False,
    )
    html = _decode_html_body(response.body)
    shell_header = _build_shell_header_html(
        conf,
        "redoc",
        "Plexichat API Reference",
        "Readable reference docs optimized for browsing routes, schemas, and models.",
    )
    html = html.replace("<body>", '<body class="plexi-openapi-page plexi-redoc-page">')
    html = html.replace(
        f'<redoc spec-url="{openapi_url}"></redoc>',
        f'{shell_header}<redoc spec-url="{openapi_url}"></redoc>',
    )
    html = html.replace("</head>", f"<style>{_build_brand_styles(conf)}</style></head>")
    return HTMLResponse(html)


def _build_sidebar_html(conf: DocsConfig, current_path: str = "") -> str:
    """Build multi-category sidebar HTML."""
    categories = {
        "Getting Started": [
            NavItem("Home", "/"),
            NavItem("Getting Started", "/getting-started"),
            NavItem("Configuration", "/configuration"),
            NavItem("Features", "/features"),
            NavItem("Permissions", "/permissions"),
            NavItem("Security", "/security"),
            NavItem("Rate Limits", "/rate-limits"),
            NavItem("Error Handling", "/errors"),
            NavItem("Data Types", "/data-types"),
        ],
        "Guides": [
            NavItem("Deployment", "/deployment"),
            NavItem("Performance", "/performance"),
            NavItem("Access Tokens", "/admin-access-tokens"),
            NavItem("OAuth Scopes", "/oauth-scopes"),
        ],
        "API Reference": [
            NavItem("Overview", "/reference"),
            NavItem("Authentication", "/reference/authentication"),
            NavItem("Users", "/reference/users"),
            NavItem("Servers", "/reference/servers"),
            NavItem("Channels", "/reference/channels"),
            NavItem("Messages", "/reference/messages"),
            NavItem("Reactions", "/reference/reactions"),
            NavItem("Relationships", "/reference/relationships"),
            NavItem("Presence", "/reference/presence"),
            NavItem("Settings", "/reference/settings"),
            NavItem("Webhooks", "/reference/webhooks"),
            NavItem("Avatars", "/reference/avatars"),
            NavItem("Emojis", "/reference/emojis"),
            NavItem("Features", "/reference/features"),
            NavItem("Search", "/reference/search"),
            NavItem("Notifications", "/reference/notifications"),
            NavItem("Polls", "/reference/polls"),
            NavItem("Voice", "/reference/voice"),
            NavItem("Media", "/reference/media"),
            NavItem("Reports", "/reference/reports"),
            NavItem("Feedback", "/reference/feedback"),
            NavItem("Telemetry", "/reference/telemetry"),
            NavItem("System", "/reference/system"),
            NavItem("Admin", "/reference/admin"),
        ],
        "WebSocket Gateway": [
            NavItem("Overview", "/websocket"),
            NavItem("Connection", "/websocket/connection"),
            NavItem("Events", "/websocket/events"),
            NavItem("Intents", "/websocket/intents"),
            NavItem("Opcodes", "/websocket/opcodes"),
            NavItem("Close Codes", "/websocket/close-codes"),
        ],
        "Help": [
            NavItem("Security Logout", "/security-logout"),
            NavItem("Access Blocked", "/access-blocked"),
        ],
    }

    html = ['<aside class="sidebar">']
    html.append('<div class="sidebar-header">')
    html.append(f'<a href="{conf.path}" class="brand-mark">PLEXI<span>CHAT</span></a>')
    html.append('<span class="sidebar-caption">Narrative Docs</span>')
    html.append(f"<h3>{conf.title}</h3>")
    html.append(f'<p class="sidebar-description">{conf.description}</p>')
    html.append("</div>")

    for category, items in categories.items():
        html.append(f'<div class="nav-category">{category}</div>')
        html.append('<ul class="nav-list">')
        for item in items:
            active = "active" if item.path == current_path else ""
            html.append(
                f'<li><a href="{conf.path}{item.path}" class="{active}">{item.label}</a></li>'
            )
        html.append("</ul>")

    html.append("</aside>")
    return "\n".join(html)


def _build_footer_html(conf: DocsConfig) -> str:
    """Build footer HTML."""
    parts = []
    if conf.features.show_version:
        app_config = get_app_config()
        parts.append(f"<span>API Version: {app_config['version']}</span>")
    if conf.features.show_last_updated:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        parts.append(f"<span>Generated: {now}</span>")
    return f'<footer class="footer">{" | ".join(parts)}</footer>' if parts else ""


def _replace_dynamic_placeholders(text: str, conf: DocsConfig) -> str:
    """Replace dynamic placeholders in documentation content."""
    # Replace API base URL placeholders
    text = text.replace("{{BASE_URL}}", conf.base_url)
    text = text.replace("{{API_BASE_URL}}", conf.base_url)

    # Replace WebSocket URL placeholders
    text = text.replace("{{WEBSOCKET_URL}}", conf.websocket_url)
    text = text.replace("{{WS_URL}}", conf.websocket_url)

    if "{{VERSION}}" in text:
        app_config = get_app_config()
        text = text.replace("{{VERSION}}", app_config["version"])

    rate_limit_tokens = (
        "{{RATE_LIMIT_DEFAULT_ROWS}}",
        "{{RATE_LIMIT_ROUTE_ROWS}}",
        "{{RATE_LIMIT_POLICY_ROWS}}",
    )
    if any(token in text for token in rate_limit_tokens):
        limits = get_api_rate_limits()
        text = text.replace(
            "{{RATE_LIMIT_DEFAULT_ROWS}}", _build_rate_limit_defaults_rows(limits)
        )
        text = text.replace(
            "{{RATE_LIMIT_ROUTE_ROWS}}", _build_rate_limit_route_rows(limits)
        )
        text = text.replace(
            "{{RATE_LIMIT_POLICY_ROWS}}", _build_rate_limit_policy_rows(limits)
        )

    gateway_intent_tokens = (
        "{{GATEWAY_DEFAULT_INTENTS}}",
        "{{GATEWAY_ALL_INTENTS}}",
        "{{GATEWAY_PRIVILEGED_INTENTS}}",
        "{{GATEWAY_INTENT_ROWS}}",
    )
    if any(token in text for token in gateway_intent_tokens):
        intents = get_gateway_intents_docs_data()
        text = text.replace(
            "{{GATEWAY_DEFAULT_INTENTS}}", str(intents.get("default_value", 0))
        )
        text = text.replace("{{GATEWAY_ALL_INTENTS}}", str(intents.get("all_value", 0)))
        text = text.replace(
            "{{GATEWAY_PRIVILEGED_INTENTS}}",
            str(intents.get("privileged_value", 0)),
        )
        text = text.replace(
            "{{GATEWAY_INTENT_ROWS}}", _build_gateway_intent_rows(intents)
        )

    permission_tokens = (
        "{{PERMISSION_CATEGORY_COUNT}}",
        "{{PERMISSION_TOTAL_COUNT}}",
        "{{PERMISSION_CATEGORY_ROWS}}",
        "{{PERMISSION_DETAIL_ROWS}}",
    )
    if any(token in text for token in permission_tokens):
        permissions = get_permissions_docs_data()
        text = text.replace(
            "{{PERMISSION_CATEGORY_COUNT}}",
            str(permissions.get("category_count", 0)),
        )
        text = text.replace(
            "{{PERMISSION_TOTAL_COUNT}}",
            str(permissions.get("permission_count", 0)),
        )
        text = text.replace(
            "{{PERMISSION_CATEGORY_ROWS}}",
            _build_permission_category_rows(permissions),
        )
        text = text.replace(
            "{{PERMISSION_DETAIL_ROWS}}", _build_permission_detail_rows(permissions)
        )

    oauth_scope_tokens = (
        "{{OAUTH_SCOPE_COUNT}}",
        "{{OAUTH_PRIVILEGED_SCOPE_COUNT}}",
        "{{OAUTH_BOT_SCOPE_COUNT}}",
        "{{OAUTH_SCOPE_ROWS}}",
    )
    if any(token in text for token in oauth_scope_tokens):
        oauth_scopes = get_oauth_scopes_docs_data()
        text = text.replace(
            "{{OAUTH_SCOPE_COUNT}}", str(oauth_scopes.get("scope_count", 0))
        )
        text = text.replace(
            "{{OAUTH_PRIVILEGED_SCOPE_COUNT}}",
            str(oauth_scopes.get("privileged_count", 0)),
        )
        text = text.replace(
            "{{OAUTH_BOT_SCOPE_COUNT}}",
            str(oauth_scopes.get("bot_required_count", 0)),
        )
        text = text.replace(
            "{{OAUTH_SCOPE_ROWS}}", _build_oauth_scope_rows(oauth_scopes)
        )

    return text


def _convert_markdown_links(text: str, conf: DocsConfig, current_path: str = "") -> str:
    """Convert markdown links to proper HTML links."""
    link_pattern = r"\[([^\]]+)\]\(([^)]+)\)"

    def replace_link(match):
        link_text = match.group(1)
        link_url = match.group(2)

        if link_url.startswith(("http://", "https://", "#", "mailto:")):
            return f'<a href="{link_url}">{link_text}</a>'

        normalized_link = link_url
        while normalized_link.startswith("../"):
            normalized_link = normalized_link[3:]
        if normalized_link.startswith("./"):
            normalized_link = normalized_link[2:]
        if normalized_link.endswith(".md"):
            normalized_link = normalized_link[:-3]

        if link_url.startswith("/"):
            return f'<a href="{conf.path}{link_url}">{link_text}</a>'

        path_mappings = {
            "getting-started": "/getting-started",
            "configuration": "/configuration",
            "deployment": "/deployment",
            "features": "/features",
            "security": "/security",
            "performance": "/performance",
            "admin-access-tokens": "/admin-access-tokens",
            "rate-limits": "/rate-limits",
            "errors": "/errors",
            "data-types": "/data-types",
            "security-logout": "/security-logout",
            "access-blocked": "/access-blocked",
            "api/index": "/reference",
            "websocket/index": "/websocket",
        }

        if normalized_link in path_mappings:
            link_url = path_mappings[normalized_link]
        elif normalized_link.startswith("api/"):
            link_url = f"/reference/{normalized_link[4:]}"
        elif normalized_link.startswith("websocket/"):
            link_url = f"/websocket/{normalized_link[10:]}"
        elif current_path == "/reference" or current_path.startswith("/reference/"):
            link_url = f"/reference/{normalized_link.lstrip('/')}"
        elif current_path == "/websocket" or current_path.startswith("/websocket/"):
            link_url = f"/websocket/{normalized_link.lstrip('/')}"
        else:
            link_url = f"/{normalized_link.lstrip('/')}"

        return f'<a href="{conf.path}{link_url}">{link_text}</a>'

    return re.sub(link_pattern, replace_link, text)


def _markdown_to_html(
    markdown_content: str, title: str, conf: DocsConfig, current_path: str = ""
) -> str:
    """Convert markdown to HTML with modern styling."""
    import html as html_module

    # Replace dynamic placeholders first (before escaping)
    markdown_content = _replace_dynamic_placeholders(markdown_content, conf)

    content = html_module.escape(markdown_content)
    content = _convert_markdown_links(content, conf, current_path)

    lines = content.split("\n")
    html_lines = []
    in_code_block = False
    in_table = False
    in_unordered_list = False
    in_ordered_list = False
    table_row_index = 0
    code_block_id = 0

    def close_lists() -> None:
        nonlocal in_unordered_list, in_ordered_list
        if in_unordered_list:
            html_lines.append("</ul>")
            in_unordered_list = False
        if in_ordered_list:
            html_lines.append("</ol>")
            in_ordered_list = False

    def format_inline(text: str) -> str:
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        return text

    for line in lines:
        if line.startswith("```"):
            close_lists()
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
                table_row_index = 0
            if not in_code_block:
                code_lang = line[3:].strip() or "text"
                code_block_id += 1
                html_lines.append(
                    f'<div class="code-block"><button class="copy-btn" data-target="code-{code_block_id}">Copy</button><pre><code id="code-{code_block_id}" class="language-{code_lang}">'
                )
                in_code_block = True
            else:
                html_lines.append("</code></pre></div>")
                in_code_block = False
            continue

        if in_code_block:
            html_lines.append(line)
            continue

        if line.startswith("### "):
            close_lists()
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
                table_row_index = 0
            html_lines.append(f"<h3>{format_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            close_lists()
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
                table_row_index = 0
            html_lines.append(f"<h2>{format_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            close_lists()
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
                table_row_index = 0
            html_lines.append(f"<h1>{format_inline(line[2:])}</h1>")
        elif line.startswith("|") and line.endswith("|"):
            close_lists()
            cells = line.split("|")[1:-1]
            if all(c.strip().startswith("-") for c in cells):
                continue
            if not in_table:
                html_lines.append('<div class="table-wrapper"><table>')
                in_table = True
                table_row_index = 0
            cell_tag = "th" if table_row_index == 0 else "td"
            html_lines.append(
                f"<tr>{''.join(f'<{cell_tag}>{format_inline(c.strip())}</{cell_tag}>' for c in cells)}</tr>"
            )
            table_row_index += 1
        elif line.startswith("- "):
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
                table_row_index = 0
            if in_ordered_list:
                html_lines.append("</ol>")
                in_ordered_list = False
            if not in_unordered_list:
                html_lines.append("<ul>")
                in_unordered_list = True
            html_lines.append(f"<li>{format_inline(line[2:])}</li>")
        elif re.match(r"^[0-9]+\. ", line):
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
                table_row_index = 0
            if in_unordered_list:
                html_lines.append("</ul>")
                in_unordered_list = False
            if not in_ordered_list:
                html_lines.append("<ol>")
                in_ordered_list = True
            ordered_item = re.sub(r"^[0-9]+\. ", "", line)
            html_lines.append(f"<li>{format_inline(ordered_item)}</li>")
        elif line.startswith("**Note:**") or line.startswith("**Important:**"):
            close_lists()
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
                table_row_index = 0
            html_lines.append(f'<div class="note">{format_inline(line)}</div>')
        elif line.strip():
            close_lists()
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
                table_row_index = 0
            html_lines.append(f"<p>{format_inline(line)}</p>")
        else:
            close_lists()
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
                table_row_index = 0
            html_lines.append("")

    close_lists()
    if in_table:
        html_lines.append("</table></div>")

    body_content = "\n".join(html_lines)
    sidebar_html = _build_sidebar_html(conf, current_path)
    footer_html = _build_footer_html(conf)
    page_title = title.split(" - ", 1)[0]
    shell_header = _build_shell_header_html(
        conf,
        "portal",
        page_title,
        "Guides, route-group overviews, and live schema entry points for the Plexichat backend.",
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>{_build_brand_styles(conf)}</style>
</head>
<body class="plexi-docs-page">
    <div class="docs-layout">
        {sidebar_html}
        <main class="docs-main">
            {shell_header}
            <section class="page-card">
                <div class="content-container">{body_content}</div>
                {footer_html}
            </section>
        </main>
    </div>
    <script>
        document.querySelectorAll('.copy-btn').forEach(btn => {{
            btn.addEventListener('click', async () => {{
                const code = document.getElementById(btn.dataset.target).textContent;
                await navigator.clipboard.writeText(code);
                const originalText = btn.textContent;
                btn.textContent = 'Copied';
                setTimeout(() => btn.textContent = originalText, 2000);
            }});
        }});
    </script>
</body>
</html>"""


async def _serve_page(
    request: Request, file_path: Path, title: str, current_path: str = ""
) -> HTMLResponse:
    conf = _runtime_docs_config(request, get_docs_config())

    try:
        mtime_ns = file_path.stat().st_mtime_ns
    except FileNotFoundError:
        raise HTTPException(404, detail="Page not found")

    source_key = f"{file_path.resolve()}|{mtime_ns}"
    html_key = _build_html_cache_key(source_key, title, current_path, conf)

    if conf.cache.enabled and conf.cache.cache_html:
        cached_html = _get_cached_value(_html_cache, html_key, conf.cache.ttl_seconds)
        if cached_html is not None:
            return HTMLResponse(cached_html)

    content: Optional[str] = None
    if conf.cache.enabled and conf.cache.cache_markdown:
        content = _get_cached_value(_docs_cache, source_key, conf.cache.ttl_seconds)

    if content is None:
        content = file_path.read_text(encoding="utf-8")
        if conf.cache.enabled and conf.cache.cache_markdown and content:
            _set_cached_value(_docs_cache, source_key, content, conf.cache.max_entries)

    if not content:
        raise HTTPException(404, detail="Page not found")

    html = _markdown_to_html(content, f"{title} - {conf.title}", conf, current_path)
    if conf.cache.enabled and conf.cache.cache_html:
        _set_cached_value(_html_cache, html_key, html, conf.cache.max_entries)

    return HTMLResponse(html)


@router.get("")
@router.get("/")
async def docs_index(request: Request):
    """
    Serve the documentation homepage.
    """
    return await _serve_page(request, _doc_path("index.md"), "Home", "/")


@router.get("/getting-started")
async def docs_getting_started(request: Request):
    """
    Serve the 'Getting Started' documentation page.
    """
    return await _serve_page(
        request, _doc_path("getting-started.md"), "Getting Started", "/getting-started"
    )


@router.get("/deployment")
async def docs_deployment(request: Request):
    """
    Serve the 'Deployment' documentation page.
    """
    return await _serve_page(
        request, _doc_path("deployment.md"), "Deployment", "/deployment"
    )


@router.get("/configuration")
async def docs_configuration(request: Request):
    """
    Serve the 'Configuration' documentation page.
    """
    return await _serve_page(
        request, _doc_path("configuration.md"), "Configuration", "/configuration"
    )


@router.get("/features")
async def docs_features(request: Request):
    """Serve the feature overview page."""
    return await _serve_page(request, _doc_path("features.md"), "Features", "/features")


@router.get("/permissions")
async def docs_permissions(request: Request):
    """Serve the permissions reference page."""
    return await _serve_page(
        request, _doc_path("permissions.md"), "Permissions", "/permissions"
    )


@router.get("/security")
async def docs_security(request: Request):
    """Serve the security guidance page."""
    return await _serve_page(request, _doc_path("security.md"), "Security", "/security")


@router.get("/performance")
async def docs_performance(request: Request):
    """Serve the performance guidance page."""
    return await _serve_page(
        request, _doc_path("performance.md"), "Performance", "/performance"
    )


@router.get("/admin-access-tokens")
async def docs_admin_access_tokens(request: Request):
    """Serve the API access token page."""
    return await _serve_page(
        request,
        _doc_path("admin-access-tokens.md"),
        "Admin Access Tokens",
        "/admin-access-tokens",
    )


@router.get("/oauth-scopes")
async def docs_oauth_scopes(request: Request):
    """Serve the OAuth scopes reference page."""
    return await _serve_page(
        request, _doc_path("oauth-scopes.md"), "OAuth Scopes", "/oauth-scopes"
    )


@router.get("/reference")
async def docs_api_reference(request: Request):
    """
    Serve the API reference index page.
    """
    return await _serve_page(
        request, _doc_path("api/index.md"), "API Reference", "/reference"
    )


@router.get("/reference/{page}")
async def docs_api_page(request: Request, page: str):
    """
    Serve a specific API reference documentation page.
    """
    return await _serve_page(
        request, _doc_path(f"api/{page}.md"), page.title(), f"/reference/{page}"
    )


@router.get("/websocket")
async def docs_websocket_index(request: Request):
    """
    Serve the WebSocket documentation index page.
    """
    return await _serve_page(
        request, _doc_path("websocket/index.md"), "WebSocket", "/websocket"
    )


@router.get("/websocket/{page}")
async def docs_websocket_page(request: Request, page: str):
    """
    Serve a specific WebSocket documentation page.
    """
    return await _serve_page(
        request, _doc_path(f"websocket/{page}.md"), page.title(), f"/websocket/{page}"
    )


@router.get("/rate-limits")
async def docs_rate_limits(request: Request):
    """
    Serve the rate limits documentation page.
    """
    return await _serve_page(
        request, _doc_path("rate-limits.md"), "Rate Limits", "/rate-limits"
    )


@router.get("/errors")
async def docs_errors(request: Request):
    """
    Serve the 'Errors' documentation page.
    """
    return await _serve_page(request, _doc_path("errors.md"), "Errors", "/errors")


@router.get("/security-logout")
async def docs_security_logout(request: Request):
    """
    Serve the 'Security Logout' documentation page.
    """
    return await _serve_page(
        request,
        _doc_path("security-logout.md"),
        "Security Logout",
        "/security-logout",
    )


@router.get("/access-blocked")
async def docs_access_blocked(request: Request):
    """
    Serve the 'Access Blocked' documentation page.
    """
    return await _serve_page(
        request,
        _doc_path("access-blocked.md"),
        "Access Blocked",
        "/access-blocked",
    )


@router.get("/data-types")
async def docs_data_types(request: Request):
    """
    Serve the 'Data Types' documentation page.
    """
    return await _serve_page(
        request, _doc_path("data-types.md"), "Data Types", "/data-types"
    )
