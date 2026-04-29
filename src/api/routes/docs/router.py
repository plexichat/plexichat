"""
Documentation routes - Serve API documentation with dynamic rate limit info.

This module provides a configurable documentation server that:
- Serves markdown documentation as HTML with a modern sidebar layout
- Dynamically loads rate limits from actual config
- Has its own configurable rate limiting
- Supports caching, theming, and logging
"""

import time
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .config import DocsConfig, get_docs_config, is_docs_enabled, _runtime_docs_config
from .renderer import markdown_to_html
from .navigation import build_sidebar_html, build_shell_header_html, build_footer_html
from .theme import build_brand_styles

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
    source_key: str, title: str, current_path: str, conf: DocsConfig
) -> str:
    """Build a stable cache key for rendered HTML output."""
    return "|".join((source_key, title, current_path, repr(conf)))


def _doc_path(relative_path: str) -> Path:
    """Resolve a documentation file path relative to the backend docs root."""
    return DOCS_ROOT / relative_path


def clear_docs_cache() -> bool:
    """Clear documentation caches."""
    global _docs_cache, _html_cache
    _docs_cache.clear()
    _html_cache.clear()
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
    }


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

    html_content = markdown_to_html(
        content, f"{title} - {conf.title}", conf, current_path
    )
    sidebar_html = build_sidebar_html(conf, current_path)
    footer_html = build_footer_html(conf)
    page_title = title.split(" - ", 1)[0]
    shell_header = build_shell_header_html(
        conf,
        "portal",
        page_title,
        "Guides, route-group overviews, and live schema entry points for the Plexichat backend.",
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - {conf.title}</title>
    <style>{build_brand_styles(conf)}</style>
</head>
<body class="plexi-docs-page">
    <div class="docs-layout">
        {sidebar_html}
        <main class="docs-main">
            {shell_header}
            <section class="page-card">
                <div class="content-container">{html_content}</div>
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
        
        function toggleNavCategory(element) {{
            const navList = element.nextElementSibling;
            navList.classList.toggle('collapsed');
            element.classList.toggle('collapsed');
        }}
    </script>
</body>
</html>"""

    if conf.cache.enabled and conf.cache.cache_html:
        _set_cached_value(_html_cache, html_key, html, conf.cache.max_entries)

    return HTMLResponse(html)


@router.get("")
@router.get("/")
async def docs_index(request: Request):
    """Serve the documentation homepage."""
    return await _serve_page(request, _doc_path("index.md"), "Home", "/")


@router.get("/getting-started")
async def docs_getting_started(request: Request):
    """Serve the 'Getting Started' documentation page."""
    return await _serve_page(
        request, _doc_path("getting-started.md"), "Getting Started", "/getting-started"
    )


@router.get("/deployment")
async def docs_deployment(request: Request):
    """Serve the 'Deployment' documentation page."""
    return await _serve_page(
        request, _doc_path("deployment.md"), "Deployment", "/deployment"
    )


@router.get("/configuration")
async def docs_configuration(request: Request):
    """Serve the 'Configuration' documentation page."""
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


@router.get("/keyrings")
async def docs_keyrings(request: Request):
    """Serve the keyring and KEK migration guide."""
    return await _serve_page(
        request,
        _doc_path("keyrings.md"),
        "Keyrings and KEK Migration",
        "/keyrings",
    )


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
    """Serve the API reference index page."""
    return await _serve_page(
        request, _doc_path("api/index.md"), "API Reference", "/reference"
    )


@router.get("/reference/{page}")
async def docs_api_page(request: Request, page: str):
    """Serve a specific API reference documentation page."""
    return await _serve_page(
        request, _doc_path(f"api/{page}.md"), page.title(), f"/reference/{page}"
    )


@router.get("/websocket")
async def docs_websocket_index(request: Request):
    """Serve the WebSocket documentation index page."""
    return await _serve_page(
        request, _doc_path("websocket/index.md"), "WebSocket", "/websocket"
    )


@router.get("/websocket/{page}")
async def docs_websocket_page(request: Request, page: str):
    """Serve a specific WebSocket documentation page."""
    return await _serve_page(
        request, _doc_path(f"websocket/{page}.md"), page.title(), f"/websocket/{page}"
    )


@router.get("/rate-limits")
async def docs_rate_limits(request: Request):
    """Serve the rate limits documentation page."""
    return await _serve_page(
        request, _doc_path("rate-limits.md"), "Rate Limits", "/rate-limits"
    )


@router.get("/errors")
async def docs_errors(request: Request):
    """Serve the 'Errors' documentation page."""
    return await _serve_page(request, _doc_path("errors.md"), "Errors", "/errors")


@router.get("/security-logout")
async def docs_security_logout(request: Request):
    """Serve the 'Security Logout' documentation page."""
    return await _serve_page(
        request,
        _doc_path("end-user/security-logout.md"),
        "Security Logout",
        "/security-logout",
    )


@router.get("/access-blocked")
async def docs_access_blocked(request: Request):
    """Serve the 'Access Blocked' documentation page."""
    return await _serve_page(
        request,
        _doc_path("end-user/access-blocked.md"),
        "Access Blocked",
        "/access-blocked",
    )


@router.get("/data-types")
async def docs_data_types(request: Request):
    """Serve the 'Data Types' documentation page."""
    return await _serve_page(
        request, _doc_path("data-types.md"), "Data Types", "/data-types"
    )


@router.get("/default-config")
async def docs_default_config(request: Request):
    """Serve the default configuration reference page."""
    return await _serve_page(
        request,
        _doc_path("default-config.md"),
        "Default Configuration",
        "/default-config",
    )


@router.get("/config-authentication")
async def docs_config_authentication(request: Request):
    """Serve the authentication configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-authentication.md"),
        "Authentication Configuration",
        "/config-authentication",
    )


@router.get("/config-database")
async def docs_config_database(request: Request):
    """Serve the database configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-database.md"),
        "Database Configuration",
        "/config-database",
    )


@router.get("/config-redis")
async def docs_config_redis(request: Request):
    """Serve the Redis configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-redis.md"),
        "Redis Configuration",
        "/config-redis",
    )


@router.get("/config-media")
async def docs_config_media(request: Request):
    """Serve the media configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-media.md"),
        "Media Configuration",
        "/config-media",
    )


@router.get("/config-voice")
async def docs_config_voice(request: Request):
    """Serve the voice configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-voice.md"),
        "Voice Configuration",
        "/config-voice",
    )


@router.get("/config-websocket")
async def docs_config_websocket(request: Request):
    """Serve the WebSocket configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-websocket.md"),
        "WebSocket Configuration",
        "/config-websocket",
    )


@router.get("/config-search")
async def docs_config_search(request: Request):
    """Serve the search configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-search.md"),
        "Search Configuration",
        "/config-search",
    )


@router.get("/config-rate-limiting")
async def docs_config_rate_limiting(request: Request):
    """Serve the rate limiting configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-rate-limiting.md"),
        "Rate Limiting Configuration",
        "/config-rate-limiting",
    )


@router.get("/config-api")
async def docs_config_api(request: Request):
    """Serve the API & server configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-api.md"),
        "API & Server Configuration",
        "/config-api",
    )


@router.get("/config-email")
async def docs_config_email(request: Request):
    """Serve the email configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-email.md"),
        "Email Configuration",
        "/config-email",
    )


@router.get("/config-embeds")
async def docs_config_embeds(request: Request):
    """Serve the embeds configuration guide."""
    return await _serve_page(
        request,
        _doc_path("deployment/configuration/config-embeds.md"),
        "Embeds Configuration",
        "/config-embeds",
    )


@router.get("/end-user/getting-started")
async def docs_end_user_getting_started(request: Request):
    """Serve the end-user getting started guide."""
    return await _serve_page(
        request,
        _doc_path("end-user/getting-started.md"),
        "User Guide: Getting Started",
        "/end-user/getting-started",
    )


@router.get("/deployment/overview")
async def docs_deployment_overview(request: Request):
    """Serve the deployment overview page."""
    return await _serve_page(
        request,
        _doc_path("deployment/overview.md"),
        "Deployment Overview",
        "/deployment/overview",
    )


@router.get("/deployment/requirements")
async def docs_deployment_requirements(request: Request):
    """Serve the deployment requirements page."""
    return await _serve_page(
        request,
        _doc_path("deployment/requirements.md"),
        "Deployment Requirements",
        "/deployment/requirements",
    )


@router.get("/deployment/getting-started")
async def docs_deployment_getting_started(request: Request):
    """Serve the deployment getting started page."""
    return await _serve_page(
        request,
        _doc_path("deployment/getting-started.md"),
        "Deployment Getting Started",
        "/deployment/getting-started",
    )


@router.get("/deployment/index")
async def docs_deployment_index(request: Request):
    """Serve the deployment index page."""
    return await _serve_page(
        request,
        _doc_path("deployment/index.md"),
        "Deployment Index",
        "/deployment/index",
    )


@router.get("/deployment/postgres-migration")
async def docs_deployment_postgres_migration(request: Request):
    """Serve the PostgreSQL migration guide page."""
    return await _serve_page(
        request,
        _doc_path("deployment/postgres-migration.md"),
        "PostgreSQL Migration Guide",
        "/deployment/postgres-migration",
    )


@router.get("/deployment/versioning")
async def docs_deployment_versioning(request: Request):
    """Serve the versioning and updates guide page."""
    return await _serve_page(
        request,
        _doc_path("deployment/versioning.md"),
        "Versioning and Updates",
        "/deployment/versioning",
    )


@router.get("/migrations")
async def docs_migrations(request: Request):
    """Serve the migrations guide page."""
    return await _serve_page(
        request,
        _doc_path("migrations.md"),
        "Database Migrations Guide",
        "/migrations",
    )


@router.get("/migration-reference")
async def docs_migration_reference(request: Request):
    """Serve the migration reference page."""
    return await _serve_page(
        request,
        _doc_path("migration-reference.md"),
        "Migration Reference",
        "/migration-reference",
    )


@router.get("/admin")
async def docs_admin(request: Request):
    """Serve the admin documentation page."""
    return await _serve_page(
        request,
        _doc_path("admin/index.md"),
        "Admin Documentation",
        "/admin",
    )


@router.get("/admin/getting-started")
async def docs_admin_getting_started(request: Request):
    """Serve the admin getting started page."""
    return await _serve_page(
        request,
        _doc_path("admin/getting-started.md"),
        "Admin Getting Started",
        "/admin/getting-started",
    )


@router.get("/admin/approval-workflows")
async def docs_admin_approval_workflows(request: Request):
    """Serve the admin approval workflows page."""
    return await _serve_page(
        request,
        _doc_path("admin/approval-workflows.md"),
        "Approval Workflows",
        "/admin/approval-workflows",
    )


@router.get("/admin/audit-logging")
async def docs_admin_audit_logging(request: Request):
    """Serve the admin audit logging page."""
    return await _serve_page(
        request,
        _doc_path("admin/audit-logging.md"),
        "Audit Logging",
        "/admin/audit-logging",
    )


@router.get("/admin/rbac")
async def docs_admin_rbac(request: Request):
    """Serve the admin RBAC page."""
    return await _serve_page(
        request,
        _doc_path("admin/rbac.md"),
        "Role-Based Access Control",
        "/admin/rbac",
    )


@router.get("/admin/server-management")
async def docs_admin_server_management(request: Request):
    """Serve the admin server management page."""
    return await _serve_page(
        request,
        _doc_path("admin/server-management.md"),
        "Server Management",
        "/admin/server-management",
    )


@router.get("/admin/operations")
async def docs_admin_operations(request: Request):
    """Serve the admin operations page."""
    return await _serve_page(
        request,
        _doc_path("admin/operations.md"),
        "Operations",
        "/admin/operations",
    )


@router.get("/admin/troubleshooting")
async def docs_admin_troubleshooting(request: Request):
    """Serve the admin troubleshooting page."""
    return await _serve_page(
        request,
        _doc_path("admin/troubleshooting.md"),
        "Troubleshooting",
        "/admin/troubleshooting",
    )


@router.get("/admin/user-management")
async def docs_admin_user_management(request: Request):
    """Serve the admin user management page."""
    return await _serve_page(
        request,
        _doc_path("admin/user-management.md"),
        "User Management",
        "/admin/user-management",
    )


@router.get("/admin/security")
async def docs_admin_security(request: Request):
    """Serve the admin security page."""
    return await _serve_page(
        request,
        _doc_path("admin/security.md"),
        "Admin Security",
        "/admin/security",
    )


@router.get("/client-development")
async def docs_client_development(request: Request):
    """Serve the client development index page."""
    return await _serve_page(
        request,
        _doc_path("client-development/index.md"),
        "Client Development",
        "/client-development",
    )


@router.get("/client-development/websocket")
async def docs_client_websocket(request: Request):
    """Serve the client WebSocket development page."""
    return await _serve_page(
        request,
        _doc_path("client-development/websocket.md"),
        "Client WebSocket Development",
        "/client-development/websocket",
    )


@router.get("/end-user")
async def docs_end_user_index(request: Request):
    """Serve the end-user documentation index page."""
    return await _serve_page(
        request,
        _doc_path("end-user/index.md"),
        "End User Documentation",
        "/end-user",
    )


@router.get("/end-user/passkeys")
async def docs_end_user_passkeys(request: Request):
    """Serve the end-user passkeys page."""
    return await _serve_page(
        request,
        _doc_path("end-user/passkeys.md"),
        "Passkeys",
        "/end-user/passkeys",
    )


@router.get("/end-user/password-guidance")
async def docs_end_user_password_guidance(request: Request):
    """Serve the end-user password guidance page."""
    return await _serve_page(
        request,
        _doc_path("end-user/password-guidance.md"),
        "Password Guidance",
        "/end-user/password-guidance",
    )


@router.get("/end-user/permissions")
async def docs_end_user_permissions(request: Request):
    """Serve the end-user permissions page."""
    return await _serve_page(
        request,
        _doc_path("end-user/permissions.md"),
        "Permissions",
        "/end-user/permissions",
    )


@router.get("/end-user/two-factor-authentication")
async def docs_end_user_2fa(request: Request):
    """Serve the end-user 2FA page."""
    return await _serve_page(
        request,
        _doc_path("end-user/two-factor-authentication.md"),
        "Two-Factor Authentication",
        "/end-user/two-factor-authentication",
    )


@router.get("/reference/notifications")
async def docs_api_notifications(request: Request):
    """Serve the notifications API documentation page."""
    return await _serve_page(
        request,
        _doc_path("api/notifications.md"),
        "Notifications API",
        "/reference/notifications",
    )


@router.get("/reference/polls")
async def docs_api_polls(request: Request):
    """Serve the polls API documentation page."""
    return await _serve_page(
        request,
        _doc_path("api/polls.md"),
        "Polls API",
        "/reference/polls",
    )


@router.get("/reference/voice")
async def docs_api_voice(request: Request):
    """Serve the voice API documentation page."""
    return await _serve_page(
        request,
        _doc_path("api/voice.md"),
        "Voice API",
        "/reference/voice",
    )


@router.get("/reference/media")
async def docs_api_media(request: Request):
    """Serve the media API documentation page."""
    return await _serve_page(
        request,
        _doc_path("api/media.md"),
        "Media API",
        "/reference/media",
    )


@router.get("/reference/reports")
async def docs_api_reports(request: Request):
    """Serve the reports API documentation page."""
    return await _serve_page(
        request,
        _doc_path("api/reports.md"),
        "Reports API",
        "/reference/reports",
    )


@router.get("/reference/feedback")
async def docs_api_feedback(request: Request):
    """Serve the feedback API documentation page."""
    return await _serve_page(
        request,
        _doc_path("api/feedback.md"),
        "Feedback API",
        "/reference/feedback",
    )


@router.get("/reference/telemetry")
async def docs_api_telemetry(request: Request):
    """Serve the telemetry API documentation page."""
    return await _serve_page(
        request,
        _doc_path("api/telemetry.md"),
        "Telemetry API",
        "/reference/telemetry",
    )


@router.get("/reference/system")
async def docs_api_system(request: Request):
    """Serve the system API documentation page."""
    return await _serve_page(
        request,
        _doc_path("api/system.md"),
        "System API",
        "/reference/system",
    )


@router.get("/reference/admin")
async def docs_api_admin(request: Request):
    """Serve the admin API documentation page."""
    return await _serve_page(
        request,
        _doc_path("api/admin.md"),
        "Admin API",
        "/reference/admin",
    )
