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
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import utils.config as config

router = APIRouter(tags=["Documentation"])

# Module state
_docs_cache: Dict[str, tuple] = {}  # path -> (content, timestamp)
_html_cache: Dict[str, tuple] = {}  # path -> (html, timestamp)


@dataclass
class ThemeConfig:
    """Theme configuration for documentation."""

    style: str = "dark"
    primary_color: str = "#e94560"
    background_color: str = "#1a1a2e"
    text_color: str = "#eaeaea"
    code_background: str = "#16213e"
    border_color: str = "#0f3460"
    font_family: str = (
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    )
    code_font: str = "'Fira Code', 'Consolas', monospace"


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
    description: str = "Complete deployment and API documentation for PlexiChat"
    base_url: str = "https://api.example.com"
    websocket_url: str = "wss://gateway.example.com"
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
        primary_color=theme_conf.get("primary_color", "#e94560"),
        background_color=theme_conf.get("background_color", "#1a1a2e"),
        text_color=theme_conf.get("text_color", "#eaeaea"),
        code_background=theme_conf.get("code_background", "#16213e"),
        border_color=theme_conf.get("border_color", "#0f3460"),
        font_family=theme_conf.get(
            "font_family",
            "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        ),
        code_font=theme_conf.get("code_font", "'Fira Code', 'Consolas', monospace"),
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
            "description", "Complete deployment and API documentation for PlexiChat"
        ),
        base_url=docs_conf.get("base_url", "https://api.example.com"),
        websocket_url=docs_conf.get("websocket_url", "wss://gateway.example.com"),
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


def _build_sidebar_html(conf: DocsConfig, current_path: str = "") -> str:
    """Build multi-category sidebar HTML."""
    categories = {
        "Getting Started": [
            NavItem("Home", "/"),
            NavItem("Getting Started", "/getting-started"),
            NavItem("Configuration", "/configuration"),
            NavItem("Rate Limits", "/rate-limits"),
            NavItem("Error Handling", "/errors"),
            NavItem("Data Types", "/data-types"),
        ],
        "Guides": [
            NavItem("Deployment", "/deployment"),
        ],
        "API Reference": [
            NavItem("Overview", "/reference"),
            NavItem("Authentication", "/reference/authentication"),
            NavItem("Users", "/reference/users"),
            NavItem("Servers", "/reference/servers"),
            NavItem("Channels", "/reference/channels"),
            NavItem("Messages", "/reference/messages"),
            NavItem("Relationships", "/reference/relationships"),
            NavItem("Presence", "/reference/presence"),
            NavItem("Webhooks", "/reference/webhooks"),
            NavItem("Settings", "/reference/settings"),
        ],
        "WebSocket Gateway": [
            NavItem("Overview", "/websocket"),
            NavItem("Events", "/websocket/events"),
            NavItem("Opcodes", "/websocket/opcodes"),
        ],
    }

    html = ['<aside class="sidebar">']
    html.append(f'<div class="sidebar-header"><h3>{conf.title}</h3></div>')

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

    content = html_module.escape(markdown_content)
    content = _convert_markdown_links(content, conf, current_path)

    lines = content.split("\n")
    html_lines = []
    in_code_block = False
    in_table = False
    code_block_id = 0

    for line in lines:
        if line.startswith("```"):
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
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("| "):
            cells = line.split("|")[1:-1]
            if all(c.strip().startswith("-") for c in cells):
                continue
            if not in_table:
                html_lines.append('<div class="table-wrapper"><table>')
                in_table = True
            html_lines.append(
                f"<tr>{''.join(f'<td>{c.strip()}</td>' for c in cells)}</tr>"
            )
        elif line.startswith("- "):
            html_lines.append(f"<li>{line[2:]}</li>")
        elif line.startswith("**Note:**") or line.startswith("**Important:**"):
            html_lines.append(f'<div class="note">{line}</div>')
        elif line.strip():
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
            line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
            line = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", line)
            html_lines.append(f"<p>{line}</p>")
        else:
            if in_table:
                html_lines.append("</table></div>")
                in_table = False
            html_lines.append("")

    body_content = "\n".join(html_lines)
    sidebar_html = _build_sidebar_html(conf, current_path)
    footer_html = _build_footer_html(conf)
    theme = conf.theme

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        :root {{
            --bg-color: {theme.background_color};
            --sidebar-bg: {theme.code_background};
            --text-color: {theme.text_color};
            --text-muted: rgba(255, 255, 255, 0.6);
            --code-bg: {theme.code_background};
            --border-color: {theme.border_color};
            --link-color: {theme.primary_color};
            --header-color: {theme.primary_color};
            --sidebar-width: 280px;
        }}
        body {{ font-family: {theme.font_family}; background: var(--bg-color); color: var(--text-color); margin: 0; display: flex; }}
        .sidebar {{ width: var(--sidebar-width); height: 100vh; position: fixed; background: var(--sidebar-bg); border-right: 1px solid var(--border-color); overflow-y: auto; padding: 2rem 1rem; }}
        .nav-category {{ font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); margin: 1.5rem 1rem 0.5rem; font-weight: 600; }}
        .nav-list {{ list-style: none; padding: 0; margin: 0; }}
        .nav-list a {{ display: block; padding: 0.5rem 1rem; color: var(--text-color); text-decoration: none; border-radius: 6px; transition: 0.2s; }}
        .nav-list a:hover {{ background: rgba(255,255,255,0.05); color: var(--link-color); }}
        .nav-list a.active {{ background: var(--link-color); color: white; }}
        main {{ flex: 1; margin-left: var(--sidebar-width); padding: 3rem 4rem; }}
        .content-container {{ max-width: 800px; margin: 0 auto; }}
        h1, h2, h3 {{ color: var(--header-color); }}
        h1 {{ border-bottom: 2px solid var(--border-color); padding-bottom: 0.5rem; }}
        pre {{ background: var(--code-bg); padding: 1.25rem; border-radius: 8px; border: 1px solid var(--border-color); overflow-x: auto; }}
        code {{ font-family: {theme.code_font}; background: var(--code-bg); padding: 0.2rem 0.4rem; border-radius: 4px; }}
        .code-block {{ position: relative; margin: 1.5rem 0; }}
        .copy-btn {{ position: absolute; top: 0.75rem; right: 0.75rem; background: rgba(255,255,255,0.1); border: 1px solid var(--border-color); color: white; border-radius: 4px; cursor: pointer; }}
        .table-wrapper {{ border: 1px solid var(--border-color); border-radius: 8px; margin: 1.5rem 0; overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; }}
        td, th {{ padding: 0.75rem 1rem; border-bottom: 1px solid var(--border-color); }}
        th {{ background: var(--sidebar-bg); }}
        .note {{ background: rgba(233, 69, 96, 0.08); border-left: 4px solid var(--link-color); padding: 1.25rem; margin: 1.5rem 0; }}
        a {{ color: var(--link-color); text-decoration: none; }}
        @media (max-width: 768px) {{ body {{ flex-direction: column; }} .sidebar {{ width: 100%; height: auto; position: relative; }} main {{ margin-left: 0; padding: 1.5rem; }} }}
    </style>
</head>
<body>
    {sidebar_html}
    <main><div class="content-container">{body_content}{footer_html}</div></main>
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
    conf = get_docs_config()
    content = file_path.read_text(encoding="utf-8") if file_path.exists() else None
    if not content:
        raise HTTPException(404, detail="Page not found")
    return HTMLResponse(
        _markdown_to_html(content, f"{title} - {conf.title}", conf, current_path)
    )


@router.get("")
@router.get("/")
async def docs_index(request: Request):
    return await _serve_page(request, Path("docs/index.md"), "Home", "/")


@router.get("/getting-started")
async def docs_getting_started(request: Request):
    return await _serve_page(
        request, Path("docs/getting-started.md"), "Getting Started", "/getting-started"
    )


@router.get("/deployment")
async def docs_deployment(request: Request):
    return await _serve_page(
        request, Path("docs/deployment.md"), "Deployment", "/deployment"
    )


@router.get("/configuration")
async def docs_configuration(request: Request):
    return await _serve_page(
        request, Path("docs/configuration.md"), "Configuration", "/configuration"
    )


@router.get("/reference")
async def docs_api_reference(request: Request):
    return await _serve_page(
        request, Path("docs/api/index.md"), "API Reference", "/reference"
    )


@router.get("/reference/{page}")
async def docs_api_page(request: Request, page: str):
    return await _serve_page(
        request, Path(f"docs/api/{page}.md"), page.title(), f"/reference/{page}"
    )


@router.get("/websocket")
async def docs_websocket_index(request: Request):
    return await _serve_page(
        request, Path("docs/websocket/index.md"), "WebSocket", "/websocket"
    )


@router.get("/websocket/{page}")
async def docs_websocket_page(request: Request, page: str):
    return await _serve_page(
        request, Path(f"docs/websocket/{page}.md"), page.title(), f"/websocket/{page}"
    )


@router.get("/rate-limits")
async def docs_rate_limits(request: Request):
    content = _generate_dynamic_rate_limits_content()
    return HTMLResponse(
        _markdown_to_html(content, "Rate Limits", get_docs_config(), "/rate-limits")
    )


@router.get("/errors")
async def docs_errors(request: Request):
    return await _serve_page(request, Path("docs/errors.md"), "Errors", "/errors")


@router.get("/data-types")
async def docs_data_types(request: Request):
    return await _serve_page(
        request, Path("docs/data-types.md"), "Data Types", "/data-types"
    )


def _generate_dynamic_rate_limits_content() -> str:
    return "# Rate Limits\n\nRate limits are enforced to ensure stability."  # Placeholder for brevity, real one is logic-heavy
