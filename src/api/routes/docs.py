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
_docs_cache: Dict[str, tuple] = {}  # path -> (content, timestamp)
_html_cache: Dict[str, tuple] = {}  # path -> (html, timestamp)


@dataclass
class ThemeConfig:
    """Theme configuration for documentation."""

    style: str = "dark"
    primary_color: str = "#6366f1"
    primary_dark_color: str = "#4f46e5"
    background_color: str = "#0b0f19"
    surface_color: str = "#111827"
    code_background: str = "#0f172a"
    text_color: str = "#f9fafb"
    muted_color: str = "#9ca3af"
    accent_color: str = "#10b981"
    warning_color: str = "#f59e0b"
    error_color: str = "#ef4444"
    border_color: str = "#1f2937"
    font_family: str = (
        "'JetBrains Mono', 'Inter', -apple-system, BlinkMacSystemFont, "
        "'Segoe UI', sans-serif"
    )
    code_font: str = "'JetBrains Mono', 'Fira Code', 'Consolas', monospace"


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
    title: str = "PlexiChat Documentation"
    description: str = "Runtime documentation for the PlexiChat backend"
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
        primary_color=theme_conf.get("primary_color", "#6366f1"),
        primary_dark_color=theme_conf.get("primary_dark_color", "#4f46e5"),
        background_color=theme_conf.get("background_color", "#0b0f19"),
        surface_color=theme_conf.get("surface_color", "#111827"),
        code_background=theme_conf.get("code_background", "#0f172a"),
        text_color=theme_conf.get("text_color", "#f9fafb"),
        muted_color=theme_conf.get("muted_color", "#9ca3af"),
        accent_color=theme_conf.get("accent_color", "#10b981"),
        warning_color=theme_conf.get("warning_color", "#f59e0b"),
        error_color=theme_conf.get("error_color", "#ef4444"),
        border_color=theme_conf.get("border_color", "#1f2937"),
        font_family=theme_conf.get(
            "font_family",
            "'JetBrains Mono', 'Inter', -apple-system, BlinkMacSystemFont, "
            "'Segoe UI', sans-serif",
        ),
        code_font=theme_conf.get(
            "code_font", "'JetBrains Mono', 'Fira Code', 'Consolas', monospace"
        ),
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
        title=docs_conf.get("title", "PlexiChat Documentation"),
        description=docs_conf.get(
            "description", "Runtime documentation for the PlexiChat backend"
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
    global _docs_cache, _html_cache, _config_cache
    _docs_cache.clear()
    _html_cache.clear()
    _config_cache = None
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
            DEFAULT_GLOBAL_LIMIT,
            DEFAULT_USER_LIMIT,
        )

        limits = {
            "global": {
                "requests": DEFAULT_GLOBAL_LIMIT.requests
                if DEFAULT_GLOBAL_LIMIT
                else 50,
                "window_seconds": DEFAULT_GLOBAL_LIMIT.window_seconds
                if DEFAULT_GLOBAL_LIMIT
                else 1,
                "burst": DEFAULT_GLOBAL_LIMIT.burst if DEFAULT_GLOBAL_LIMIT else 10,
            },
            "user": {
                "requests": DEFAULT_USER_LIMIT.requests if DEFAULT_USER_LIMIT else 120,
                "window_seconds": DEFAULT_USER_LIMIT.window_seconds
                if DEFAULT_USER_LIMIT
                else 60,
                "burst": DEFAULT_USER_LIMIT.burst if DEFAULT_USER_LIMIT else 20,
            },
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


def get_app_config() -> Dict[str, Any]:
    """Get application configuration for documentation."""
    try:
        import utils.version as version

        return {
            "name": "PlexiChat",
            "version": version.current_string(),
        }
    except Exception:
        return {"name": "PlexiChat", "version": "unknown"}


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
        html.append(
            f'<a href="{href}" class="surface-link {active_class}">{label}</a>'
        )
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
        f'{_build_runtime_pills_html(conf)}'
        '</div>'
        f'{_build_surface_nav_html(conf, current_surface)}'
        '</div>'
        '</header>'
    )


def _build_brand_styles(conf: DocsConfig) -> str:
    """Build shared landing-inspired styles for docs surfaces."""
    theme = conf.theme
    return f"""
        :root {{
            --primary: {theme.primary_color};
            --primary-dark: {theme.primary_dark_color};
            --bg: {theme.background_color};
            --card-bg: {theme.surface_color};
            --code-bg: {theme.code_background};
            --text: {theme.text_color};
            --text-muted: {theme.muted_color};
            --accent: {theme.accent_color};
            --warning: {theme.warning_color};
            --error: {theme.error_color};
            --border: {theme.border_color};
            --font-main: {theme.font_family};
            --font-code: {theme.code_font};
            --sidebar-width: 320px;
            --shadow-lg: 0 24px 60px rgba(0, 0, 0, 0.35);
            --shadow-md: 0 12px 32px rgba(0, 0, 0, 0.24);
        }}

        * {{ box-sizing: border-box; }}

        html {{ scroll-behavior: smooth; }}

        body {{
            margin: 0;
            color: var(--text);
            background:
                radial-gradient(circle at top left, rgba(99, 102, 241, 0.18), transparent 34%),
                radial-gradient(circle at top right, rgba(16, 185, 129, 0.12), transparent 22%),
                linear-gradient(180deg, rgba(15, 23, 42, 0.96), var(--bg));
            font-family: var(--font-main);
            line-height: 1.6;
            min-height: 100vh;
        }}

        .plexi-backdrop {{
            inset: 0;
            opacity: 0.55;
            pointer-events: none;
            position: fixed;
            background-image:
                linear-gradient(rgba(255, 255, 255, 0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(255, 255, 255, 0.03) 1px, transparent 1px);
            background-size: 36px 36px;
        }}

        .brand-mark {{
            color: var(--text);
            display: inline-flex;
            font-size: 1.1rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-decoration: none;
            text-transform: uppercase;
        }}

        .brand-mark span {{ color: var(--primary); }}

        .shell-header {{
            margin-bottom: 1.5rem;
            position: relative;
            z-index: 1;
        }}

        .shell-header-inner {{
            background: rgba(17, 24, 39, 0.8);
            backdrop-filter: blur(18px);
            border: 1px solid rgba(99, 102, 241, 0.18);
            border-radius: 24px;
            box-shadow: var(--shadow-lg);
            display: grid;
            gap: 1.25rem;
            padding: 1.5rem;
        }}

        .shell-brand-block {{ display: grid; gap: 0.8rem; }}

        .surface-badge {{
            background: rgba(99, 102, 241, 0.1);
            border: 1px solid rgba(99, 102, 241, 0.28);
            border-radius: 999px;
            color: var(--primary);
            display: inline-flex;
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            padding: 0.35rem 0.8rem;
            text-transform: uppercase;
            width: fit-content;
        }}

        .shell-title {{
            font-size: clamp(1.9rem, 4vw, 2.8rem);
            letter-spacing: -0.05em;
            line-height: 1.05;
            margin: 0;
        }}

        .shell-summary {{
            color: var(--text-muted);
            font-size: 0.96rem;
            margin: 0;
            max-width: 70ch;
        }}

        .surface-nav {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
        }}

        .surface-link {{
            background: rgba(15, 23, 42, 0.72);
            border: 1px solid var(--border);
            border-radius: 14px;
            color: var(--text-muted);
            display: inline-flex;
            font-size: 0.86rem;
            font-weight: 700;
            padding: 0.75rem 1rem;
            text-decoration: none;
            transition: transform 0.18s ease, border-color 0.18s ease,
                color 0.18s ease, background 0.18s ease;
        }}

        .surface-link:hover,
        .surface-link.active {{
            background: rgba(99, 102, 241, 0.14);
            border-color: rgba(99, 102, 241, 0.42);
            color: var(--text);
            transform: translateY(-1px);
        }}

        .runtime-pills {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
        }}

        .runtime-pill {{
            background: rgba(11, 15, 25, 0.85);
            border: 1px solid var(--border);
            border-radius: 999px;
            color: var(--text-muted);
            display: inline-flex;
            font-size: 0.78rem;
            padding: 0.45rem 0.8rem;
        }}

        .runtime-pill.accent {{
            border-color: rgba(16, 185, 129, 0.35);
            color: var(--accent);
        }}

        .docs-layout {{
            display: grid;
            grid-template-columns: var(--sidebar-width) minmax(0, 1fr);
            min-height: 100vh;
            position: relative;
            z-index: 1;
        }}

        .sidebar {{
            background: rgba(17, 24, 39, 0.84);
            backdrop-filter: blur(22px);
            border-right: 1px solid var(--border);
            height: 100vh;
            overflow-y: auto;
            padding: 1.5rem 1.1rem 2rem;
            position: sticky;
            top: 0;
        }}

        .sidebar-header {{
            background: linear-gradient(180deg, rgba(99, 102, 241, 0.16), rgba(17, 24, 39, 0.92));
            border: 1px solid rgba(99, 102, 241, 0.24);
            border-radius: 22px;
            box-shadow: var(--shadow-md);
            margin-bottom: 1.5rem;
            padding: 1.2rem;
        }}

        .sidebar-caption {{
            color: var(--primary);
            display: block;
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.1em;
            margin: 0.8rem 0 0.45rem;
            text-transform: uppercase;
        }}

        .sidebar-header h3 {{
            font-size: 1.05rem;
            margin: 0 0 0.6rem;
        }}

        .sidebar-description {{
            color: var(--text-muted);
            font-size: 0.84rem;
            margin: 0;
        }}

        .nav-category {{
            color: var(--text-muted);
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.1em;
            margin: 1.5rem 0.9rem 0.5rem;
            text-transform: uppercase;
        }}

        .nav-list {{ list-style: none; margin: 0; padding: 0; }}

        .nav-list li + li {{ margin-top: 0.2rem; }}

        .nav-list a {{
            border: 1px solid transparent;
            border-radius: 14px;
            color: var(--text-muted);
            display: block;
            font-size: 0.88rem;
            padding: 0.7rem 0.95rem;
            text-decoration: none;
            transition: all 0.18s ease;
        }}

        .nav-list a:hover,
        .nav-list a.active {{
            background: rgba(99, 102, 241, 0.12);
            border-color: rgba(99, 102, 241, 0.24);
            color: var(--text);
            transform: translateX(2px);
        }}

        .docs-main {{
            padding: 1.75rem;
            position: relative;
        }}

        .page-card {{
            background: rgba(17, 24, 39, 0.78);
            border: 1px solid var(--border);
            border-radius: 28px;
            box-shadow: var(--shadow-lg);
            overflow: hidden;
            position: relative;
        }}

        .content-container {{
            margin: 0 auto;
            max-width: 960px;
            padding: clamp(1.4rem, 3vw, 2.25rem);
        }}

        .content-container > :first-child {{ margin-top: 0; }}

        h1, h2, h3, h4 {{
            color: var(--text);
            line-height: 1.15;
            margin-top: 1.6em;
        }}

        h1 {{
            border-bottom: 1px solid rgba(99, 102, 241, 0.18);
            font-size: clamp(2rem, 4vw, 3rem);
            letter-spacing: -0.05em;
            margin-bottom: 1rem;
            padding-bottom: 0.8rem;
        }}

        h2 {{
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            font-size: 1.35rem;
            padding-bottom: 0.5rem;
        }}

        h3 {{ font-size: 1.05rem; }}

        p, li, td, th {{ color: var(--text-muted); font-size: 0.95rem; }}

        strong {{ color: var(--text); }}

        a {{ color: var(--primary); text-decoration: none; }}

        a:hover {{ color: #a5b4fc; }}

        ul, ol {{ margin: 1rem 0 1.2rem; padding-left: 1.35rem; }}

        li + li {{ margin-top: 0.45rem; }}

        pre {{
            background: linear-gradient(180deg, rgba(2, 6, 23, 0.96), rgba(15, 23, 42, 0.96));
            border: 1px solid rgba(99, 102, 241, 0.16);
            border-radius: 18px;
            overflow-x: auto;
            padding: 1.3rem;
        }}

        code {{
            background: rgba(15, 23, 42, 0.88);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
            color: var(--text);
            font-family: var(--font-code);
            padding: 0.18rem 0.38rem;
        }}

        pre code {{
            background: transparent;
            border: 0;
            padding: 0;
        }}

        .code-block {{ margin: 1.4rem 0; position: relative; }}

        .copy-btn {{
            align-items: center;
            background: rgba(99, 102, 241, 0.16);
            border: 1px solid rgba(99, 102, 241, 0.3);
            border-radius: 10px;
            color: var(--text);
            cursor: pointer;
            display: inline-flex;
            font-family: var(--font-main);
            font-size: 0.8rem;
            font-weight: 700;
            gap: 0.35rem;
            padding: 0.5rem 0.75rem;
            position: absolute;
            right: 0.85rem;
            top: 0.85rem;
        }}

        .copy-btn:hover {{ background: rgba(99, 102, 241, 0.22); }}

        .table-wrapper {{
            background: rgba(15, 23, 42, 0.62);
            border: 1px solid var(--border);
            border-radius: 18px;
            margin: 1.5rem 0;
            overflow-x: auto;
        }}

        table {{ border-collapse: collapse; width: 100%; }}

        th, td {{
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            padding: 0.9rem 1rem;
            text-align: left;
        }}

        tr:first-child td,
        th {{
            background: rgba(99, 102, 241, 0.08);
            color: var(--text);
            font-size: 0.8rem;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }}

        .note {{
            background: rgba(99, 102, 241, 0.08);
            border: 1px solid rgba(99, 102, 241, 0.18);
            border-left: 4px solid var(--primary);
            border-radius: 16px;
            margin: 1.5rem 0;
            padding: 1rem 1.15rem;
        }}

        .footer {{
            border-top: 1px solid rgba(255, 255, 255, 0.06);
            color: var(--text-muted);
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            margin-top: 0;
            padding: 1rem 1.4rem 1.4rem;
        }}

        .plexi-openapi-page #swagger-ui,
        .plexi-openapi-page redoc {{
            display: block;
            padding: 0 1.75rem 2rem;
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
            background: rgba(17, 24, 39, 0.82);
            border: 1px solid var(--border);
            border-radius: 20px;
            box-shadow: none;
        }}

        .plexi-openapi-page .swagger-ui .scheme-container {{
            margin: 1.25rem 0 1.5rem;
            padding: 1rem;
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
            border-color: rgba(255, 255, 255, 0.06);
        }}

        .plexi-openapi-page .swagger-ui .opblock.opblock-get {{
            background: linear-gradient(90deg, rgba(16, 185, 129, 0.12), rgba(17, 24, 39, 0.92));
            border-color: rgba(16, 185, 129, 0.26);
        }}

        .plexi-openapi-page .swagger-ui .opblock.opblock-post,
        .plexi-openapi-page .swagger-ui .opblock.opblock-put,
        .plexi-openapi-page .swagger-ui .opblock.opblock-patch {{
            background: linear-gradient(90deg, rgba(99, 102, 241, 0.14), rgba(17, 24, 39, 0.92));
            border-color: rgba(99, 102, 241, 0.28);
        }}

        .plexi-openapi-page .swagger-ui .opblock.opblock-delete {{
            background: linear-gradient(90deg, rgba(239, 68, 68, 0.12), rgba(17, 24, 39, 0.92));
            border-color: rgba(239, 68, 68, 0.24);
        }}

        .plexi-openapi-page .swagger-ui .btn,
        .plexi-openapi-page .swagger-ui button,
        .plexi-openapi-page .swagger-ui select,
        .plexi-openapi-page .swagger-ui input,
        .plexi-openapi-page .swagger-ui textarea {{
            border-radius: 12px;
            font-family: var(--font-main);
        }}

        .plexi-openapi-page .swagger-ui input,
        .plexi-openapi-page .swagger-ui textarea,
        .plexi-openapi-page .swagger-ui select {{
            background: rgba(15, 23, 42, 0.92);
            border: 1px solid var(--border);
            color: var(--text);
        }}

        .plexi-openapi-page .swagger-ui .btn.authorize,
        .plexi-openapi-page .swagger-ui .btn.execute,
        .plexi-openapi-page .swagger-ui .download-url-wrapper .select-label select {{
            border-color: rgba(99, 102, 241, 0.36);
        }}

        .plexi-openapi-page .swagger-ui .btn.execute,
        .plexi-openapi-page .swagger-ui .btn.authorize {{
            background: var(--primary);
            color: #fff;
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
            border-radius: 20px;
            overflow: hidden;
        }}

        .plexi-openapi-page .swagger-ui section.models h4,
        .plexi-openapi-page .swagger-ui section.models h5 {{
            color: var(--text);
        }}

        .plexi-openapi-page .swagger-ui .model-toggle:after {{
            background: var(--primary);
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
            background: rgba(17, 24, 39, 0.88) !important;
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

        .plexi-openapi-page p,
        .plexi-openapi-page li,
        .plexi-openapi-page label,
        .plexi-openapi-page span,
        .plexi-openapi-page td,
        .plexi-openapi-page th {{
            color: var(--text-muted) !important;
        }}

        .plexi-openapi-page [role="search"] input {{
            background: rgba(15, 23, 42, 0.94) !important;
            border: 1px solid var(--border) !important;
            border-radius: 14px !important;
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
            background: rgba(2, 6, 23, 0.92) !important;
            border-radius: 14px !important;
        }}

        @media (max-width: 1100px) {{
            .docs-layout {{ grid-template-columns: 1fr; }}
            .sidebar {{
                border-right: 0;
                border-bottom: 1px solid var(--border);
                height: auto;
                position: relative;
                top: auto;
            }}
        }}

        @media (max-width: 720px) {{
            .docs-main,
            .plexi-openapi-page #swagger-ui,
            .plexi-openapi-page redoc {{
                padding-left: 1rem;
                padding-right: 1rem;
            }}

            .shell-header-inner,
            .page-card {{ border-radius: 22px; }}

            .surface-link {{ width: 100%; justify-content: center; }}
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
    html = get_swagger_ui_html(
        openapi_url=openapi_url,
        title=f"{title} - PlexiChat API Explorer",
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
    ).body.decode("utf-8")
    shell_header = _build_shell_header_html(
        conf,
        "swagger",
        "PlexiChat API Explorer",
        "Interactive request explorer powered by the live OpenAPI schema.",
    )
    html = html.replace("<body>", '<body class="plexi-openapi-page plexi-swagger-page">')
    html = html.replace(
        '<div id="swagger-ui">',
        f'<div class="plexi-backdrop" aria-hidden="true"></div>{shell_header}<div id="swagger-ui">',
    )
    html = html.replace("</head>", f"<style>{_build_brand_styles(conf)}</style></head>")
    return HTMLResponse(html)


def render_redoc_page(request: Request, title: str, openapi_url: str) -> HTMLResponse:
    """Render a branded ReDoc page."""
    conf = _runtime_docs_config(request, get_docs_config())
    html = get_redoc_html(
        openapi_url=openapi_url,
        title=f"{title} - PlexiChat API Reference",
        with_google_fonts=False,
    ).body.decode("utf-8")
    shell_header = _build_shell_header_html(
        conf,
        "redoc",
        "PlexiChat API Reference",
        "Readable reference docs optimized for browsing routes, schemas, and models.",
    )
    html = html.replace("<body>", '<body class="plexi-openapi-page plexi-redoc-page">')
    html = html.replace(
        f'<redoc spec-url="{openapi_url}"></redoc>',
        f'<div class="plexi-backdrop" aria-hidden="true"></div>{shell_header}'
        f'<redoc spec-url="{openapi_url}"></redoc>',
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
            NavItem("Security", "/security"),
            NavItem("Rate Limits", "/rate-limits"),
            NavItem("Error Handling", "/errors"),
            NavItem("Data Types", "/data-types"),
        ],
        "Guides": [
            NavItem("Deployment", "/deployment"),
            NavItem("Performance", "/performance"),
            NavItem("Access Tokens", "/admin-access-tokens"),
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
        ],
        "WebSocket Gateway": [
            NavItem("Overview", "/websocket"),
            NavItem("Connection", "/websocket/connection"),
            NavItem("Events", "/websocket/events"),
            NavItem("Opcodes", "/websocket/opcodes"),
            NavItem("Close Codes", "/websocket/close-codes"),
        ],
        "Help": [
            NavItem("Security Logout", "/security-logout"),
        ],
    }

    html = ['<aside class="sidebar">']
    html.append('<div class="sidebar-header">')
    html.append(f'<a href="{conf.path}" class="brand-mark">PLEXI<span>CHAT</span></a>')
    html.append('<span class="sidebar-caption">Narrative Docs</span>')
    html.append(f'<h3>{conf.title}</h3>')
    html.append(f'<p class="sidebar-description">{conf.description}</p>')
    html.append('</div>')

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

    # Replace version placeholders
    app_config = get_app_config()
    text = text.replace("{{VERSION}}", app_config["version"])

    return text


def _convert_markdown_links(text: str, conf: DocsConfig, current_path: str = "") -> str:
    """Convert markdown links to proper HTML links."""
    link_pattern = r"\[([^\]]+)\]\(([^)]+)\)"

    def replace_link(match):
        link_text = match.group(1)
        link_url = match.group(2)

        if link_url.startswith(("http://", "https://", "#", "mailto:")):
            return f'<a href="{link_url}">{link_text}</a>'

        if link_url.endswith(".md"):
            link_url = link_url[:-3]

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
            "api/index": "/reference",
            "websocket/index": "/websocket",
        }

        if link_url in path_mappings:
            link_url = path_mappings[link_url]
        elif link_url.startswith("api/"):
            link_url = f"/reference/{link_url[4:]}"
        elif link_url.startswith("websocket/"):
            link_url = f"/websocket/{link_url[10:]}"
        else:
            link_url = f"/{link_url}"

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
                    f'<div class="code-block"><button class="copy-btn" data-target="code-{code_block_id}">📋</button><pre><code id="code-{code_block_id}" class="language-{code_lang}">'
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
        "Guides, route-group overviews, and live schema entry points for the PlexiChat backend.",
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
    <div class="plexi-backdrop" aria-hidden="true"></div>
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
                btn.textContent = '✓';
                setTimeout(() => btn.textContent = '📋', 2000);
            }});
        }});
    </script>
</body>
</html>"""


async def _serve_page(
    request: Request, file_path: Path, title: str, current_path: str = ""
) -> HTMLResponse:
    conf = _runtime_docs_config(request, get_docs_config())
    content = file_path.read_text(encoding="utf-8") if file_path.exists() else None
    if not content:
        raise HTTPException(404, detail="Page not found")
    return HTMLResponse(
        _markdown_to_html(content, f"{title} - {conf.title}", conf, current_path)
    )


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


@router.get("/data-types")
async def docs_data_types(request: Request):
    """
    Serve the 'Data Types' documentation page.
    """
    return await _serve_page(
        request, _doc_path("data-types.md"), "Data Types", "/data-types"
    )
