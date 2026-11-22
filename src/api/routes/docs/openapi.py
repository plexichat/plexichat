"""
OpenAPI (Swagger/ReDoc) page rendering with consistent sidebar.

This module wraps FastAPI's built-in Swagger UI and ReDoc renderers with our branding.
"""

import html as _html

from fastapi import Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse

from .navigation import (
    build_sidebar_html,
    build_footer_html,
    build_shell_header_html,
)
from .theme import build_brand_styles


def _decode_html_body(body: bytes | memoryview | str) -> str:
    """Decode a FastAPI/Starlette HTML body into text."""
    if isinstance(body, str):
        return body
    if isinstance(body, memoryview):
        return body.tobytes().decode("utf-8")
    return body.decode("utf-8")


# Swagger UI and ReDoc fetch the spec after the page has rendered. Keep these
# fragments as plain strings (not f-strings) so CSS/JS braces stay literal.
_SPEC_STATUS_STYLE = """
<style>
    .docs-spec-status,
    .docs-spec-error {
        align-items: center;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        display: flex;
        flex-wrap: wrap;
        gap: var(--spacing-sm) var(--spacing-md);
        margin: var(--spacing-lg) 0;
        padding: var(--spacing-md) var(--spacing-lg);
    }
    .docs-spec-status { color: var(--text-muted); }
    .docs-spec-status[hidden],
    .docs-spec-error[hidden] { display: none; }
    .docs-spec-error { border-left: 4px solid #ef4444; color: var(--text); }
    .docs-spec-spinner {
        animation: plexi-spec-spin 0.8s linear infinite;
        border: 2px solid var(--border);
        border-top-color: var(--accent);
        border-radius: 50%;
        display: inline-block;
        height: 14px;
        width: 14px;
    }
    @keyframes plexi-spec-spin { to { transform: rotate(360deg); } }
    .docs-spec-error button {
        background: var(--accent);
        border: 1px solid var(--accent);
        border-radius: var(--radius-sm);
        color: #fff;
        cursor: pointer;
        font: inherit;
        padding: var(--spacing-xs) var(--spacing-md);
    }
    @media (prefers-reduced-motion: reduce) {
        .docs-spec-spinner { animation: none; }
    }
</style>
"""

_SPEC_STATUS_SCRIPT = """
<script>
(function () {
    'use strict';
    var status = document.getElementById('docs-spec-status');
    var error = document.getElementById('docs-spec-error');
    var detail = document.getElementById('docs-spec-error-detail');
    if (!status || !error) return;
    function fail(reason) {
        status.hidden = true;
        if (detail && reason) detail.textContent = reason;
        error.hidden = false;
    }
    var url = status.getAttribute('data-spec-url');
    if (!url) { fail('No specification URL is configured.'); return; }
    fetch(url)
        .then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        })
        .then(function (spec) {
            if (!spec || !spec.paths || Object.keys(spec.paths).length === 0) {
                throw new Error('the specification is empty');
            }
            status.hidden = true;
        })
        .catch(function (err) {
            fail('Check your connection and try again. (' +
                 (err && err.message ? err.message : 'unknown error') + ')');
        });
})();
</script>
"""


def _build_spec_status_html(openapi_url: str) -> str:
    """Accessible loading / failure banner for the async spec fetch.

    Without this, a slow, failed or empty spec leaves an empty content column
    with no explanation and no way to recover. The URL is HTML-escaped before
    it is written into the data attribute so config values cannot inject markup.
    """
    spec_url = _html.escape(openapi_url, quote=True)
    banner = (
        '<div class="docs-spec-status" id="docs-spec-status" '
        f'data-spec-url="{spec_url}" role="status" aria-live="polite">'
        '<span class="docs-spec-spinner" aria-hidden="true"></span>'
        "Loading API specification…</div>"
        '<div class="docs-spec-error" id="docs-spec-error" role="alert" hidden>'
        "<strong>The API specification could not be loaded.</strong>"
        '<span id="docs-spec-error-detail">Check your connection and try again.</span>'
        '<button type="button" onclick="location.reload()">Retry</button>'
        "</div>"
    )
    return banner + _SPEC_STATUS_STYLE + _SPEC_STATUS_SCRIPT


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
    html = html.replace(
        '<div id="swagger-ui">',
        _build_spec_status_html(openapi_url) + '<div id="swagger-ui">',
        1,
    )

    # Build sidebar with Swagger as active
    sidebar_html = build_sidebar_html(conf, current_path="/swagger")
    footer_html = build_footer_html(conf)
    # The narrative docs pages open with a branded title, intro and surface
    # navigation; the endpoint explorer had none, so it opened as an untitled
    # wall of operations. Reuse the same shell header for a consistent first
    # impression and a visible route to the other documentation surfaces.
    shell_header = build_shell_header_html(
        conf,
        "swagger",
        "API Explorer",
        "Interactive REST reference for every /api/v1 endpoint, grouped by tag. Expand a category to inspect or try a request.",
    )

    html = html.replace(
        "<body>", '<body class="plexi-openapi-page plexi-swagger-page"><a class="skip-link" href="#main-content">Skip to content</a>'
    )
    html = html.replace(
        '<div id="swagger-ui">',
        f'<div class="docs-layout">{sidebar_html}<main class="docs-main" id="main-content"><div class="docs-content">{shell_header}<div class="content-container"><div id="swagger-ui">',
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
    html = html.replace(
        f'<redoc spec-url="{openapi_url}"></redoc>',
        _build_spec_status_html(openapi_url)
        + f'<redoc spec-url="{openapi_url}"></redoc>',
        1,
    )

    # Build sidebar with ReDoc as active
    sidebar_html = build_sidebar_html(conf, current_path="/redoc")
    footer_html = build_footer_html(conf)
    # Match the narrative pages and the Swagger explorer: a title, an intro and
    # links to the other docs surfaces instead of a bare ReDoc widget.
    shell_header = build_shell_header_html(
        conf,
        "redoc",
        "API Reference",
        "Searchable REST reference for every /api/v1 endpoint, grouped by tag.",
    )

    html = html.replace("<body>", '<body class="plexi-openapi-page plexi-redoc-page"><a class="skip-link" href="#main-content">Skip to content</a>')
    html = html.replace(
        f'<redoc spec-url="{openapi_url}"></redoc>',
        f'<div class="docs-layout">{sidebar_html}<main class="docs-main" id="main-content"><div class="docs-content">{shell_header}<div class="content-container"><redoc spec-url="{openapi_url}"></redoc>',
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
