"""
Navigation HTML builders for documentation.

This module handles sidebar and header navigation HTML generation.
"""

from .config import DocsConfig, NavItem
from .dynamic import get_app_config


def build_sidebar_html(conf: DocsConfig, current_path: str = "") -> str:
    """Build multi-category sidebar HTML with collapsible sections."""
    # Expanded navigation structure with admin section
    categories = {
        "Guide": [
            NavItem("Home", "/"),
            NavItem("Getting Started", "/getting-started"),
            NavItem("Deployment", "/deployment"),
            NavItem("Security", "/security"),
            NavItem("Performance", "/performance"),
        ],
        "Reference": [
            NavItem("API Routes", "/reference"),
            NavItem("WebSocket Gateway", "/websocket"),
            NavItem("Rate Limits", "/rate-limits"),
            NavItem("Error Codes", "/errors"),
            NavItem("Data Types", "/data-types"),
            NavItem("Permissions", "/permissions"),
            NavItem("OAuth Scopes", "/oauth-scopes"),
        ],
        "Configuration": [
            NavItem("Overview", "/configuration"),
            NavItem("Default Reference", "/default-config"),
        ],
        "Admin": [
            NavItem("Admin Overview", "/admin"),
            NavItem("Getting Started", "/admin/getting-started"),
            NavItem("RBAC System", "/admin/rbac"),
            NavItem("Approval Workflows", "/admin/approval-workflows"),
            NavItem("Audit Logging", "/admin/audit-logging"),
            NavItem("User Management", "/admin/user-management"),
            NavItem("Server Management", "/admin/server-management"),
            NavItem("Operations", "/admin/operations"),
            NavItem("Security", "/admin/security"),
            NavItem("Troubleshooting", "/admin/troubleshooting"),
        ],
        "Database": [
            NavItem("Migrations Guide", "/migrations"),
            NavItem("Migration Reference", "/migration-reference"),
            NavItem("PostgreSQL Migration", "/deployment/postgres-migration"),
        ],
        "Operations": [
            NavItem("Versioning and Updates", "/deployment/versioning"),
        ],
    }

    html = ['<aside class="sidebar">']
    html.append('<div class="sidebar-header">')
    html.append(f'<a href="{conf.path}" class="brand-mark">PLEXI<span>CHAT</span></a>')
    html.append('<span class="sidebar-caption">Documentation</span>')
    html.append(f"<h3>{conf.title}</h3>")
    html.append(f'<p class="sidebar-description">{conf.description}</p>')
    html.append("</div>")

    # Add surface navigation at top of sidebar
    html.append(build_surface_nav_html(conf, current_path, in_sidebar=True))

    for category, items in categories.items():
        html.append(
            f'<div class="nav-category" onclick="toggleNavCategory(this)">{category}</div>'
        )
        html.append('<ul class="nav-list">')
        for item in items:
            active = "active" if item.path == current_path else ""
            html.append(
                f'<li><a href="{conf.path}{item.path}" class="{active}">{item.label}</a></li>'
            )
        html.append("</ul>")

    html.append("</aside>")
    return "\n".join(html)


def build_surface_nav_html(
    conf: DocsConfig, current_surface: str, in_sidebar: bool = False
) -> str:
    """Build the shared top-level docs surface navigation."""
    try:
        from src.api.config import get_api_config

        api_conf = get_api_config()
        api_paths = {
            "docs_url": api_conf.docs_url or "/docs",
            "redoc_url": api_conf.redoc_url or "/redoc",
            "openapi_url": api_conf.openapi_url or "/openapi.json",
        }
    except Exception:
        api_paths = {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }

    items = [
        ("Narrative", conf.path, current_surface == "portal"),
        ("Swagger", api_paths["docs_url"], current_surface == "swagger"),
        ("ReDoc", api_paths["redoc_url"], current_surface == "redoc"),
        ("Schema", api_paths["openapi_url"], False),
    ]

    if in_sidebar:
        html = ['<div class="surface-nav">']
        for label, href, active in items:
            if not href:
                continue
            active_class = "active" if active else ""
            html.append(
                f'<a href="{href}" class="surface-link {active_class}">{label}</a>'
            )
        html.append("</div>")
        return "\n".join(html)
    else:
        html = ['<nav class="surface-nav" aria-label="Documentation surfaces">']
        for label, href, active in items:
            if not href:
                continue
            active_class = "active" if active else ""
            html.append(
                f'<a href="{href}" class="surface-link {active_class}">{label}</a>'
            )
        html.append("</nav>")
        return "\n".join(html)


def build_runtime_pills_html(conf: DocsConfig) -> str:
    """Build runtime endpoint summary pills."""
    app_config = get_app_config()
    pills = [
        f'<span class="runtime-pill">REST {conf.base_url}</span>',
        f'<span class="runtime-pill">Gateway {conf.websocket_url}</span>',
        f'<span class="runtime-pill accent">Version {app_config["version"]}</span>',
    ]
    return f'<div class="runtime-pills">{"".join(pills)}</div>'


def build_shell_header_html(
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
        "</div>"
        f"{build_surface_nav_html(conf, current_surface, in_sidebar=False)}"
        "</div>"
        "</header>"
    )


def build_footer_html(conf: DocsConfig) -> str:
    """Build footer HTML with runtime info."""
    parts = []
    if conf.features.show_version:
        app_config = get_app_config()
        parts.append(f"<span>API Version: {app_config['version']}</span>")
    if conf.features.show_last_updated:
        import datetime

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        parts.append(f"<span>Generated: {now}</span>")

    footer_content = " | ".join(parts) if parts else ""

    # Add runtime pills to footer
    runtime_pills = build_runtime_pills_html(conf)

    return (
        f'<footer class="footer">{footer_content}<div class="footer-runtime">{runtime_pills}</div></footer>'
        if footer_content
        else f'<footer class="footer"><div class="footer-runtime">{runtime_pills}</div></footer>'
    )
