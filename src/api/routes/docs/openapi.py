"""
OpenAPI (Swagger/ReDoc) page rendering with consistent sidebar.

This module wraps FastAPI's built-in Swagger UI and ReDoc renderers with our branding.
"""

from fastapi import Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse

from .navigation import build_sidebar_html, build_footer_html
from .theme import build_brand_styles


def _decode_html_body(body: bytes | memoryview | str) -> str:
    """Decode a FastAPI/Starlette HTML body into text."""
    if isinstance(body, str):
        return body
    if isinstance(body, memoryview):
        return body.tobytes().decode("utf-8")
    return body.decode("utf-8")


def render_swagger_ui_page(
    request: Request,
    title: str,
    openapi_url: str,
    oauth2_redirect_url: str | None = None,
) -> HTMLResponse:
    """Render a branded Swagger UI page with sidebar."""
    from .config import get_docs_config
    from .config import _runtime_docs_config

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

    # Build sidebar with Swagger as active
    sidebar_html = build_sidebar_html(conf, current_path="/swagger")
    footer_html = build_footer_html(conf)

    html = html.replace(
        "<body>", '<body class="plexi-openapi-page plexi-swagger-page">'
    )
    html = html.replace(
        '<div id="swagger-ui">',
        f'<div class="docs-layout">{sidebar_html}<main class="docs-main"><div class="docs-content"><div class="content-container"><div id="swagger-ui">',
    )
    html = html.replace(
        "</body>", f"</div></div>{footer_html}</div></main></div></body>"
    )
    html = html.replace("</head>", f"<style>{build_brand_styles(conf)}</style></head>")

    dark_mode_script = """
    <button class="dark-mode-toggle" onclick="toggleDarkMode()" id="darkModeToggle" aria-label="Toggle dark mode">&#x263E;</button>
    <script>
        function toggleDarkMode() {
            var theme = document.documentElement.getAttribute('data-theme');
            if (theme === 'dark') {
                document.documentElement.removeAttribute('data-theme');
                localStorage.setItem('plexi-docs-theme', 'light');
            } else {
                document.documentElement.setAttribute('data-theme', 'dark');
                localStorage.setItem('plexi-docs-theme', 'dark');
            }
        }
        (function() {
            var saved = localStorage.getItem('plexi-docs-theme');
            if (saved === 'dark') {
                document.documentElement.setAttribute('data-theme', 'dark');
            }
        })();
    </script>
    """

    html = html.replace("</body>", f"{dark_mode_script}</body>")

    return HTMLResponse(html)


def render_redoc_page(request: Request, title: str, openapi_url: str) -> HTMLResponse:
    """Render a branded ReDoc page with sidebar."""
    from .config import get_docs_config
    from .config import _runtime_docs_config

    conf = _runtime_docs_config(request, get_docs_config())
    response = get_redoc_html(
        openapi_url=openapi_url,
        title=f"{title} - Plexichat API Reference",
        with_google_fonts=False,
    )
    html = _decode_html_body(response.body)

    # Build sidebar with ReDoc as active
    sidebar_html = build_sidebar_html(conf, current_path="/redoc")
    footer_html = build_footer_html(conf)

    html = html.replace("<body>", '<body class="plexi-openapi-page plexi-redoc-page">')
    html = html.replace(
        f'<redoc spec-url="{openapi_url}"></redoc>',
        f'<div class="docs-layout">{sidebar_html}<main class="docs-main"><div class="docs-content"><div class="content-container"><redoc spec-url="{openapi_url}"></redoc>',
    )
    html = html.replace("</body>", f"</div>{footer_html}</div></main></div></body>")
    html = html.replace("</head>", f"<style>{build_brand_styles(conf)}</style></head>")

    dark_mode_script = """
    <button class="dark-mode-toggle" onclick="toggleDarkMode()" id="darkModeToggle" aria-label="Toggle dark mode">&#x263E;</button>
    <script>
        function toggleDarkMode() {
            var theme = document.documentElement.getAttribute('data-theme');
            if (theme === 'dark') {
                document.documentElement.removeAttribute('data-theme');
                localStorage.setItem('plexi-docs-theme', 'light');
            } else {
                document.documentElement.setAttribute('data-theme', 'dark');
                localStorage.setItem('plexi-docs-theme', 'dark');
            }
        }
        (function() {
            var saved = localStorage.getItem('plexi-docs-theme');
            if (saved === 'dark') {
                document.documentElement.setAttribute('data-theme', 'dark');
            }
        })();
    </script>
    """

    html = html.replace("</body>", f"{dark_mode_script}</body>")

    return HTMLResponse(html)
