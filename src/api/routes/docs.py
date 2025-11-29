"""
Documentation routes - Serve API documentation with dynamic rate limit info.

This module provides a configurable documentation server that:
- Serves markdown documentation as HTML
- Dynamically loads rate limits from actual config
- Has its own configurable rate limiting
- Supports caching, theming, and logging
"""

import os
import re
import time
from typing import Optional, Dict, Any, List
from pathlib import Path
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, Request, Response, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import utils.logger as logger
import utils.config as config

router = APIRouter()

# Module state
_docs_cache: Dict[str, tuple] = {}  # path -> (content, timestamp)
_html_cache: Dict[str, tuple] = {}  # path -> (html, timestamp)
_request_counts: Dict[str, List[float]] = {}  # ip -> [timestamps]


@dataclass
class ThemeConfig:
    """Theme configuration for documentation."""
    style: str = "dark"
    primary_color: str = "#e94560"
    background_color: str = "#1a1a2e"
    text_color: str = "#eaeaea"
    code_background: str = "#16213e"
    border_color: str = "#0f3460"
    font_family: str = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
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
    title: str = "PlexiChat API Documentation"
    description: str = "Complete API documentation for PlexiChat messaging platform"
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
        font_family=theme_conf.get("font_family", "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"),
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
    
    # Navigation
    nav_conf = docs_conf.get("navigation", {})
    nav_items = []
    for item in nav_conf.get("items", []):
        if isinstance(item, dict):
            nav_items.append(NavItem(label=item.get("label", ""), path=item.get("path", "/")))
    if not nav_items:
        nav_items = [
            NavItem("Home", "/"),
            NavItem("Getting Started", "/getting-started"),
            NavItem("API Reference", "/reference"),
            NavItem("WebSocket", "/websocket"),
            NavItem("Rate Limits", "/rate-limits"),
        ]
    navigation = NavigationConfig(
        show_nav=nav_conf.get("show_nav", True),
        items=nav_items,
    )
    
    # Features
    feat_conf = docs_conf.get("features", {})
    features = FeaturesConfig(
        enable_raw_endpoint=feat_conf.get("enable_raw_endpoint", True),
        enable_search=feat_conf.get("enable_search", False),
        show_version=feat_conf.get("show_version", True),
        show_last_updated=feat_conf.get("show_last_updated", True),
        syntax_highlighting=feat_conf.get("syntax_highlighting", True),
    )
    
    return DocsConfig(
        enabled=docs_conf.get("enabled", True),
        path=docs_conf.get("path", "/docs/api"),
        title=docs_conf.get("title", "PlexiChat API Documentation"),
        description=docs_conf.get("description", "Complete API documentation for PlexiChat messaging platform"),
        base_url=docs_conf.get("base_url", "https://api.example.com"),
        websocket_url=docs_conf.get("websocket_url", "wss://gateway.example.com"),
        theme=theme,
        rate_limit=rate_limit,
        cache=cache,
        logging=logging_config,
        security=security,
        navigation=navigation,
        features=features,
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


def get_api_rate_limits() -> Dict[str, Any]:
    """
    Get actual API rate limits from the rate limit configuration.
    
    This dynamically loads the real rate limits so documentation is always accurate.
    """
    try:
        from src.core.ratelimit.config import (
            DEFAULT_ROUTE_LIMITS,
            DEFAULT_GLOBAL_LIMIT,
            DEFAULT_USER_LIMIT,
        )
        
        limits = {
            "global": {
                "requests": DEFAULT_GLOBAL_LIMIT.requests,
                "window_seconds": DEFAULT_GLOBAL_LIMIT.window_seconds,
                "burst": DEFAULT_GLOBAL_LIMIT.burst,
                "algorithm": DEFAULT_GLOBAL_LIMIT.algorithm.value if hasattr(DEFAULT_GLOBAL_LIMIT.algorithm, 'value') else str(DEFAULT_GLOBAL_LIMIT.algorithm),
            },
            "user": {
                "requests": DEFAULT_USER_LIMIT.requests,
                "window_seconds": DEFAULT_USER_LIMIT.window_seconds,
                "burst": DEFAULT_USER_LIMIT.burst,
                "hourly_limit": DEFAULT_USER_LIMIT.hourly_limit,
                "daily_limit": DEFAULT_USER_LIMIT.daily_limit,
                "algorithm": DEFAULT_USER_LIMIT.algorithm.value if hasattr(DEFAULT_USER_LIMIT.algorithm, 'value') else str(DEFAULT_USER_LIMIT.algorithm),
            },
            "routes": {}
        }
        
        for route, cfg in DEFAULT_ROUTE_LIMITS.items():
            limits["routes"][route] = {
                "requests": cfg.requests,
                "window_seconds": cfg.window_seconds,
                "burst": cfg.burst,
                "algorithm": cfg.algorithm.value if hasattr(cfg.algorithm, 'value') else str(cfg.algorithm),
                "hourly_limit": getattr(cfg, 'hourly_limit', None),
                "daily_limit": getattr(cfg, 'daily_limit', None),
            }
        
        return limits
    except ImportError:
        return {}
    except Exception as e:
        logger.warning(f"Failed to load rate limits for docs: {e}")
        return {}


def get_app_config() -> Dict[str, Any]:
    """Get application configuration for documentation."""
    try:
        app_conf = config.get("application", {})
        auth_conf = config.get("authentication", {})
        
        return {
            "name": app_conf.get("name", "PlexiChat"),
            "version": app_conf.get("version", "a.1.0-1"),
            "environment": app_conf.get("environment", "development"),
            "password_requirements": {
                "min_length": auth_conf.get("password", {}).get("min_length", 8),
                "require_uppercase": auth_conf.get("password", {}).get("require_uppercase", True),
                "require_lowercase": auth_conf.get("password", {}).get("require_lowercase", True),
                "require_digit": auth_conf.get("password", {}).get("require_digit", True),
                "require_special": auth_conf.get("password", {}).get("require_special", True),
            },
            "session": {
                "max_concurrent": auth_conf.get("session", {}).get("max_concurrent_sessions", 3),
                "access_token_expire_minutes": auth_conf.get("jwt", {}).get("access_token_expire_minutes", 30),
                "refresh_token_expire_days": auth_conf.get("jwt", {}).get("refresh_token_expire_days", 7),
            },
            "account_lockout": {
                "max_failed_attempts": auth_conf.get("account_lockout", {}).get("max_failed_attempts", 5),
                "lockout_duration_minutes": auth_conf.get("account_lockout", {}).get("lockout_duration_minutes", 15),
            },
        }
    except RuntimeError:
        return {}


def _check_rate_limit(request: Request, conf: DocsConfig) -> bool:
    """Check if request is rate limited."""
    if not conf.rate_limit.enabled:
        return True
    
    client_ip = request.client.host if request.client else "unknown"
    
    # Check whitelist
    if client_ip in conf.rate_limit.whitelist:
        return True
    
    now = time.time()
    window_start = now - conf.rate_limit.window_seconds
    
    if client_ip not in _request_counts:
        _request_counts[client_ip] = []
    
    # Clean old entries
    _request_counts[client_ip] = [
        ts for ts in _request_counts[client_ip] if ts > window_start
    ]
    
    # Check limit
    if len(_request_counts[client_ip]) >= conf.rate_limit.requests:
        return False
    
    # Record request
    _request_counts[client_ip].append(now)
    return True


def _get_docs_path() -> Path:
    """Get the documentation directory path."""
    project_root = Path(__file__).parent.parent.parent.parent
    docs_path = project_root / "docs"
    if docs_path.exists():
        return docs_path
    return Path("docs")


def _validate_path(path: str, conf: DocsConfig) -> bool:
    """Validate a file path for security."""
    if conf.security.block_traversal and ".." in path:
        return False
    
    # Check extension
    ext = Path(path).suffix.lower()
    if ext and ext not in conf.security.allowed_extensions:
        return False
    
    return True


def _read_markdown_file(file_path: Path, conf: DocsConfig) -> Optional[str]:
    """Read a markdown file with caching."""
    path_str = str(file_path)
    now = time.time()
    
    # Check cache
    if conf.cache.enabled and conf.cache.cache_markdown and path_str in _docs_cache:
        content, timestamp = _docs_cache[path_str]
        if now - timestamp < conf.cache.ttl_seconds:
            if conf.logging.log_cache_hits:
                logger.debug(f"Cache hit for {path_str}")
            return content
    
    # Read file
    if not file_path.exists() or not file_path.is_file():
        return None
    
    try:
        content = file_path.read_text(encoding="utf-8")
        
        # Manage cache size
        if conf.cache.enabled and conf.cache.cache_markdown:
            if len(_docs_cache) >= conf.cache.max_entries:
                # Remove oldest entry
                oldest_key = min(_docs_cache.keys(), key=lambda k: _docs_cache[k][1])
                del _docs_cache[oldest_key]
            _docs_cache[path_str] = (content, now)
        
        return content
    except Exception as e:
        if conf.logging.log_errors:
            logger.error(f"Error reading docs file {file_path}: {e}")
        return None


def _generate_dynamic_rate_limits_content() -> str:
    """Generate dynamic rate limits documentation from actual config."""
    limits = get_api_rate_limits()
    app_config = get_app_config()
    docs_conf = get_docs_config()
    
    if not limits:
        return ""
    
    content = f"""# Rate Limits

PlexiChat uses rate limiting to ensure fair usage and protect the API from abuse.

**Note:** These rate limits are dynamically loaded from the server configuration and reflect the current settings.

## Rate Limit Algorithms

| Algorithm | Description |
|-----------|-------------|
| token_bucket | Allows bursts, tokens refill over time |
| sliding_window | Smooth rate limiting over rolling window |
| fixed_window | Simple count per fixed time window |

## Global Limits

| Scope | Requests | Window | Burst | Algorithm |
|-------|----------|--------|-------|-----------|
| Per User | {limits.get('user', {}).get('requests', 120)} | {limits.get('user', {}).get('window_seconds', 60)}s | {limits.get('user', {}).get('burst', 20)} | {limits.get('user', {}).get('algorithm', 'sliding_window')} |
| Per Second | {limits.get('global', {}).get('requests', 50)} | {limits.get('global', {}).get('window_seconds', 1)}s | {limits.get('global', {}).get('burst', 10)} | {limits.get('global', {}).get('algorithm', 'token_bucket')} |

"""
    
    # Group routes by category
    auth_routes = {}
    message_routes = {}
    user_routes = {}
    server_routes = {}
    other_routes = {}
    
    for route, cfg in limits.get('routes', {}).items():
        if '/auth/' in route:
            auth_routes[route] = cfg
        elif '/messages' in route or '/channels/' in route:
            message_routes[route] = cfg
        elif '/users/' in route:
            user_routes[route] = cfg
        elif '/servers/' in route:
            server_routes[route] = cfg
        else:
            other_routes[route] = cfg
    
    if auth_routes:
        content += "## Authentication Endpoints\n\n"
        content += "| Endpoint | Requests | Window | Burst | Hourly | Daily |\n"
        content += "|----------|----------|--------|-------|--------|-------|\n"
        for route, cfg in auth_routes.items():
            hourly = cfg.get('hourly_limit') or '-'
            daily = cfg.get('daily_limit') or '-'
            content += f"| {route} | {cfg['requests']} | {cfg['window_seconds']}s | {cfg['burst']} | {hourly} | {daily} |\n"
        content += "\n"
    
    if message_routes:
        content += "## Message & Channel Endpoints\n\n"
        content += "| Endpoint | Requests | Window | Burst | Algorithm |\n"
        content += "|----------|----------|--------|-------|----------|\n"
        for route, cfg in message_routes.items():
            content += f"| {route} | {cfg['requests']} | {cfg['window_seconds']}s | {cfg['burst']} | {cfg['algorithm']} |\n"
        content += "\n"
    
    if user_routes:
        content += "## User Endpoints\n\n"
        content += "| Endpoint | Requests | Window | Burst | Hourly |\n"
        content += "|----------|----------|--------|-------|--------|\n"
        for route, cfg in user_routes.items():
            hourly = cfg.get('hourly_limit') or '-'
            content += f"| {route} | {cfg['requests']} | {cfg['window_seconds']}s | {cfg['burst']} | {hourly} |\n"
        content += "\n"
    
    if server_routes:
        content += "## Server Endpoints\n\n"
        content += "| Endpoint | Requests | Window | Burst | Daily |\n"
        content += "|----------|----------|--------|-------|-------|\n"
        for route, cfg in server_routes.items():
            daily = cfg.get('daily_limit') or '-'
            content += f"| {route} | {cfg['requests']} | {cfg['window_seconds']}s | {cfg['burst']} | {daily} |\n"
        content += "\n"
    
    if other_routes:
        content += "## Other Endpoints\n\n"
        content += "| Endpoint | Requests | Window | Burst | Algorithm |\n"
        content += "|----------|----------|--------|-------|----------|\n"
        for route, cfg in other_routes.items():
            content += f"| {route} | {cfg['requests']} | {cfg['window_seconds']}s | {cfg['burst']} | {cfg['algorithm']} |\n"
        content += "\n"
    
    # Add hourly/daily limits section
    user_hourly = limits.get('user', {}).get('hourly_limit')
    user_daily = limits.get('user', {}).get('daily_limit')
    
    if user_hourly or user_daily:
        content += "## User Aggregate Limits\n\n"
        content += "| Scope | Limit |\n"
        content += "|-------|-------|\n"
        if user_hourly:
            content += f"| Hourly | {user_hourly} requests |\n"
        if user_daily:
            content += f"| Daily | {user_daily} requests |\n"
        content += "\n"
    
    content += """## Rate Limit Headers

All responses include rate limit information:

```
X-RateLimit-Limit: 50
X-RateLimit-Remaining: 49
X-RateLimit-Reset: 1704067200
X-RateLimit-Bucket: route:POST:/channels/{id}/messages
```

| Header | Description |
|--------|-------------|
| X-RateLimit-Limit | Maximum requests in window |
| X-RateLimit-Remaining | Remaining requests |
| X-RateLimit-Reset | Unix timestamp when limit resets |
| X-RateLimit-Bucket | Bucket identifier |

## Rate Limited Response (HTTP 429)

```json
{
  "error": {
    "code": 429,
    "message": "Rate limited",
    "retry_after": 1.5
  }
}
```

| Field | Description |
|-------|-------------|
| retry_after | Seconds to wait before retrying |

## Bot Rate Limits

Bots receive a 1.2x multiplier on high-traffic routes:
- POST /channels/{id}/messages
- GET /channels/{id}/messages
- PUT/DELETE reactions

## Bypassing Rate Limits

### Internal Requests

Internal services can bypass rate limits using headers:
- `X-Internal-Request: true`
- `X-RateLimit-Bypass: <key>`

### Admin Users

Users with admin permissions (`admin.*` or `*`) are exempt from rate limits.

## Best Practices

1. **Respect rate limits** - Don't retry immediately after 429
2. **Use exponential backoff** - Increase delay between retries
3. **Cache responses** - Reduce unnecessary requests
4. **Batch operations** - Combine multiple operations when possible
5. **Monitor headers** - Track remaining requests proactively

## WebSocket Rate Limits

WebSocket connections have separate limits:

| Scope | Events | Window |
|-------|--------|--------|
| Per Connection | 120 | 60s |

Exceeding WebSocket rate limits results in close code 4008 (RATE_LIMITED).
"""
    
    return content


def _build_nav_html(conf: DocsConfig, current_path: str = "") -> str:
    """Build navigation HTML from config."""
    if not conf.navigation.show_nav:
        return ""
    
    nav_items = []
    for item in conf.navigation.items:
        active = "active" if item.path == current_path else ""
        nav_items.append(f'<a href="{conf.path}{item.path}" class="{active}">{item.label}</a>')
    
    return f'<nav class="nav">{" ".join(nav_items)}</nav>'


def _build_footer_html(conf: DocsConfig) -> str:
    """Build footer HTML."""
    parts = []
    
    if conf.features.show_version:
        app_config = get_app_config()
        version = app_config.get("version", "unknown")
        parts.append(f"<span>API Version: {version}</span>")
    
    if conf.features.show_last_updated:
        import datetime
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        parts.append(f"<span>Generated: {now}</span>")
    
    if not parts:
        return ""
    
    return f'<footer class="footer">{" | ".join(parts)}</footer>'


def _markdown_to_html(markdown_content: str, title: str, conf: DocsConfig, current_path: str = "") -> str:
    """Convert markdown to HTML with configurable styling."""
    import html as html_module
    content = html_module.escape(markdown_content)
    
    lines = content.split("\n")
    html_lines = []
    in_code_block = False
    in_table = False
    code_lang = ""
    
    for line in lines:
        if line.startswith("```"):
            if not in_code_block:
                code_lang = line[3:].strip()
                html_lines.append(f'<pre><code class="language-{code_lang}">')
                in_code_block = True
            else:
                html_lines.append("</code></pre>")
                in_code_block = False
            continue
        
        if in_code_block:
            html_lines.append(line)
            continue
        
        # Headers
        if line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("---"):
            html_lines.append("<hr>")
        elif line.startswith("| "):
            # Table handling
            cells = line.split("|")[1:-1]
            if all(c.strip().startswith("-") for c in cells):
                continue  # Skip separator row
            if not in_table:
                html_lines.append("<table>")
                in_table = True
            row = "".join(f"<td>{c.strip()}</td>" for c in cells)
            html_lines.append(f"<tr>{row}</tr>")
        elif line.startswith("- "):
            html_lines.append(f"<li>{line[2:]}</li>")
        elif line.startswith("**Note:**") or line.startswith("**Important:**"):
            html_lines.append(f'<div class="note">{line}</div>')
        elif line.strip():
            if in_table:
                html_lines.append("</table>")
                in_table = False
            # Convert inline code and bold
            line = re.sub(r"`([^`]+)`", r"<code>\1</code>", line)
            line = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", line)
            html_lines.append(f"<p>{line}</p>")
        else:
            if in_table:
                html_lines.append("</table>")
                in_table = False
            html_lines.append("")
    
    if in_table:
        html_lines.append("</table>")
    
    body = "\n".join(html_lines)
    nav_html = _build_nav_html(conf, current_path)
    footer_html = _build_footer_html(conf)
    theme = conf.theme
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{conf.description}">
    <title>{title}</title>
    <style>
        :root {{
            --bg-color: {theme.background_color};
            --text-color: {theme.text_color};
            --code-bg: {theme.code_background};
            --border-color: {theme.border_color};
            --link-color: {theme.primary_color};
            --header-color: {theme.primary_color};
        }}
        * {{ box-sizing: border-box; }}
        body {{
            font-family: {theme.font_family};
            background-color: var(--bg-color);
            color: var(--text-color);
            line-height: 1.6;
            max-width: 1000px;
            margin: 0 auto;
            padding: 2rem;
        }}
        h1, h2, h3 {{ color: var(--header-color); }}
        h1 {{ border-bottom: 2px solid var(--border-color); padding-bottom: 0.5rem; }}
        h2 {{ border-bottom: 1px solid var(--border-color); padding-bottom: 0.3rem; margin-top: 2rem; }}
        code {{
            background-color: var(--code-bg);
            padding: 0.2rem 0.4rem;
            border-radius: 4px;
            font-family: {theme.code_font};
            font-size: 0.9em;
        }}
        pre {{
            background-color: var(--code-bg);
            padding: 1rem;
            border-radius: 8px;
            overflow-x: auto;
            border: 1px solid var(--border-color);
        }}
        pre code {{ padding: 0; background: none; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 1rem 0;
        }}
        th, td {{
            border: 1px solid var(--border-color);
            padding: 0.5rem 0.75rem;
            text-align: left;
        }}
        th {{ background-color: var(--code-bg); }}
        tr:nth-child(even) {{ background-color: rgba(255,255,255,0.02); }}
        a {{ color: var(--link-color); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        hr {{ border: none; border-top: 1px solid var(--border-color); margin: 2rem 0; }}
        li {{ margin: 0.3rem 0; }}
        ul {{ padding-left: 1.5rem; }}
        .nav {{
            margin-bottom: 2rem;
            padding: 1rem;
            background-color: var(--code-bg);
            border-radius: 8px;
            display: flex;
            flex-wrap: wrap;
            gap: 1rem;
        }}
        .nav a {{
            padding: 0.5rem 1rem;
            border-radius: 4px;
            transition: background-color 0.2s;
        }}
        .nav a:hover {{ background-color: rgba(255,255,255,0.1); }}
        .nav a.active {{ background-color: var(--link-color); color: white; }}
        .footer {{
            margin-top: 3rem;
            padding-top: 1rem;
            border-top: 1px solid var(--border-color);
            font-size: 0.85em;
            color: rgba(255,255,255,0.6);
        }}
        .note {{
            background-color: rgba(233, 69, 96, 0.1);
            border-left: 4px solid var(--link-color);
            padding: 1rem;
            margin: 1rem 0;
            border-radius: 0 8px 8px 0;
        }}
        @media (max-width: 768px) {{
            body {{ padding: 1rem; }}
            .nav {{ flex-direction: column; gap: 0.5rem; }}
            table {{ font-size: 0.85em; }}
        }}
    </style>
</head>
<body>
    {nav_html}
    <main>
        {body}
    </main>
    {footer_html}
</body>
</html>"""


def _log_request(request: Request, path: str, status: int, conf: DocsConfig):
    """Log documentation request."""
    if not conf.logging.enabled or not conf.logging.log_requests:
        return
    
    if conf.logging.log_client_ip:
        client_ip = request.client.host if request.client else "unknown"
        logger.info(f"Docs request: {request.method} {path} from {client_ip} -> {status}")
    else:
        logger.info(f"Docs request: {request.method} {path} -> {status}")


async def _serve_page(request: Request, file_path: Path, title: str, current_path: str = "") -> HTMLResponse:
    """Common page serving logic."""
    conf = get_docs_config()
    
    if not conf.enabled:
        raise HTTPException(status_code=404, detail="Documentation is disabled")
    
    if not _check_rate_limit(request, conf):
        _log_request(request, current_path, 429, conf)
        raise HTTPException(status_code=429, detail="Rate limited")
    
    content = _read_markdown_file(file_path, conf)
    if not content:
        _log_request(request, current_path, 404, conf)
        raise HTTPException(status_code=404, detail="Page not found")
    
    html = _markdown_to_html(content, f"{title} - {conf.title}", conf, current_path)
    _log_request(request, current_path, 200, conf)
    return HTMLResponse(content=html)


# Routes

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def docs_index(request: Request):
    """Documentation index page."""
    docs_path = _get_docs_path()
    return await _serve_page(request, docs_path / "index.md", "Home", "/")


@router.get("/getting-started", response_class=HTMLResponse)
async def docs_getting_started(request: Request):
    """Getting started documentation."""
    docs_path = _get_docs_path()
    return await _serve_page(request, docs_path / "getting-started.md", "Getting Started", "/getting-started")


@router.get("/reference", response_class=HTMLResponse)
async def docs_api_reference(request: Request):
    """API reference index."""
    docs_path = _get_docs_path()
    return await _serve_page(request, docs_path / "api" / "index.md", "API Reference", "/reference")


@router.get("/reference/{page}", response_class=HTMLResponse)
async def docs_api_page(request: Request, page: str):
    """API reference page."""
    conf = get_docs_config()
    
    if not _validate_path(page, conf):
        raise HTTPException(status_code=400, detail="Invalid page name")
    
    docs_path = _get_docs_path()
    title = page.replace("-", " ").title()
    return await _serve_page(request, docs_path / "api" / f"{page}.md", title, f"/reference/{page}")


@router.get("/websocket", response_class=HTMLResponse)
async def docs_websocket_index(request: Request):
    """WebSocket documentation index."""
    docs_path = _get_docs_path()
    return await _serve_page(request, docs_path / "websocket" / "index.md", "WebSocket Gateway", "/websocket")


@router.get("/websocket/{page}", response_class=HTMLResponse)
async def docs_websocket_page(request: Request, page: str):
    """WebSocket documentation page."""
    conf = get_docs_config()
    
    if not _validate_path(page, conf):
        raise HTTPException(status_code=400, detail="Invalid page name")
    
    docs_path = _get_docs_path()
    title = page.replace("-", " ").title()
    return await _serve_page(request, docs_path / "websocket" / f"{page}.md", title, f"/websocket/{page}")


@router.get("/rate-limits", response_class=HTMLResponse)
async def docs_rate_limits(request: Request):
    """
    Rate limits documentation - DYNAMICALLY GENERATED from actual config.
    
    This endpoint generates rate limit documentation from the actual
    rate limit configuration, ensuring documentation is always accurate.
    """
    conf = get_docs_config()
    
    if not conf.enabled:
        raise HTTPException(status_code=404, detail="Documentation is disabled")
    
    if not _check_rate_limit(request, conf):
        _log_request(request, "/rate-limits", 429, conf)
        raise HTTPException(status_code=429, detail="Rate limited")
    
    # Generate dynamic content from actual rate limit config
    content = _generate_dynamic_rate_limits_content()
    
    if not content:
        # Fallback to static file
        docs_path = _get_docs_path()
        content = _read_markdown_file(docs_path / "rate-limits.md", conf)
    
    if not content:
        raise HTTPException(status_code=404, detail="Page not found")
    
    html = _markdown_to_html(content, f"Rate Limits - {conf.title}", conf, "/rate-limits")
    _log_request(request, "/rate-limits", 200, conf)
    return HTMLResponse(content=html)


@router.get("/errors", response_class=HTMLResponse)
async def docs_errors(request: Request):
    """Error handling documentation."""
    docs_path = _get_docs_path()
    return await _serve_page(request, docs_path / "errors.md", "Error Handling", "/errors")


@router.get("/data-types", response_class=HTMLResponse)
async def docs_data_types(request: Request):
    """Data types documentation."""
    docs_path = _get_docs_path()
    return await _serve_page(request, docs_path / "data-types.md", "Data Types", "/data-types")


# JSON API endpoints

@router.get("/api/config", response_class=JSONResponse)
async def docs_api_config(request: Request):
    """
    Get current API configuration as JSON.
    
    Returns application config, rate limits, and other settings
    that are useful for API consumers.
    """
    conf = get_docs_config()
    
    if not conf.enabled:
        raise HTTPException(status_code=404, detail="Documentation is disabled")
    
    if not _check_rate_limit(request, conf):
        raise HTTPException(status_code=429, detail="Rate limited")
    
    app_config = get_app_config()
    rate_limits = get_api_rate_limits()
    
    _log_request(request, "/api/config", 200, conf)
    return JSONResponse(content={
        "application": app_config,
        "rate_limits": rate_limits,
        "base_url": conf.base_url,
        "websocket_url": conf.websocket_url,
    })


@router.get("/api/rate-limits", response_class=JSONResponse)
async def docs_api_rate_limits_json(request: Request):
    """
    Get current rate limits as JSON.
    
    Returns the actual rate limit configuration from the server.
    """
    conf = get_docs_config()
    
    if not conf.enabled:
        raise HTTPException(status_code=404, detail="Documentation is disabled")
    
    if not _check_rate_limit(request, conf):
        raise HTTPException(status_code=429, detail="Rate limited")
    
    rate_limits = get_api_rate_limits()
    
    _log_request(request, "/api/rate-limits", 200, conf)
    return JSONResponse(content=rate_limits)


@router.get("/raw/{path:path}", response_class=JSONResponse)
async def docs_raw(request: Request, path: str):
    """
    Get raw markdown content as JSON.
    
    Useful for programmatic access to documentation.
    """
    conf = get_docs_config()
    
    if not conf.enabled:
        raise HTTPException(status_code=404, detail="Documentation is disabled")
    
    if not conf.features.enable_raw_endpoint:
        raise HTTPException(status_code=404, detail="Raw endpoint is disabled")
    
    if not _check_rate_limit(request, conf):
        raise HTTPException(status_code=429, detail="Rate limited")
    
    if not _validate_path(path, conf):
        raise HTTPException(status_code=400, detail="Invalid path")
    
    docs_path = _get_docs_path()
    
    # Add .md extension if not present
    if not path.endswith(".md"):
        path = f"{path}.md"
    
    file_path = docs_path / path
    
    content = _read_markdown_file(file_path, conf)
    if not content:
        raise HTTPException(status_code=404, detail="Page not found")
    
    _log_request(request, f"/raw/{path}", 200, conf)
    return JSONResponse(content={
        "path": path,
        "content": content,
        "title": conf.title,
    })


# Utility functions

def is_docs_enabled() -> bool:
    """Check if documentation is enabled."""
    conf = get_docs_config()
    return conf.enabled


def clear_docs_cache():
    """Clear the documentation cache."""
    global _docs_cache, _html_cache, _config_cache
    _docs_cache = {}
    _html_cache = {}
    _config_cache = None
    logger.info("Documentation cache cleared")


def get_docs_stats() -> Dict[str, Any]:
    """Get documentation server statistics."""
    conf = get_docs_config()
    return {
        "enabled": conf.enabled,
        "cache_entries": len(_docs_cache),
        "html_cache_entries": len(_html_cache),
        "rate_limit_tracked_ips": len(_request_counts),
        "config": {
            "title": conf.title,
            "path": conf.path,
            "cache_enabled": conf.cache.enabled,
            "cache_ttl": conf.cache.ttl_seconds,
            "rate_limit_enabled": conf.rate_limit.enabled,
            "rate_limit_requests": conf.rate_limit.requests,
            "rate_limit_window": conf.rate_limit.window_seconds,
        }
    }
