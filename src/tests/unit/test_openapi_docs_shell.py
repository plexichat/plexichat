"""Branded OpenAPI surfaces must share the docs shell header.

The narrative docs pages open with a title, an intro and surface navigation
from build_shell_header_html(). The Swagger explorer (/docs) and ReDoc page
(/redoc) did not, so their endpoint lists opened as untitled walls of
operations with no visible route to the other documentation surfaces.
"""

from starlette.requests import Request

from src.api.routes.docs.openapi import render_redoc_page, render_swagger_ui_page


def _request():
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": "/docs",
        "raw_path": b"/docs",
        "query_string": b"",
        "headers": [],
        "client": ("1.2.3.4", 1),
    }
    return Request(scope)


def test_swagger_explorer_has_title_intro_and_surface_nav():
    html = render_swagger_ui_page(
        _request(), "Plexichat", "/openapi.json", "/docs/oauth2-redirect"
    ).body.decode("utf-8")

    assert html.count('<h1 class="shell-title"') == 1
    assert '<h1 class="shell-title">API Explorer</h1>' in html
    assert 'class="shell-summary"' in html
    assert 'class="surface-nav"' in html
    assert html.index('class="shell-header"') < html.index('id="swagger-ui"')


def test_redoc_reference_has_title_intro_and_surface_nav():
    html = render_redoc_page(_request(), "Plexichat", "/openapi.json").body.decode(
        "utf-8"
    )

    assert html.count('<h1 class="shell-title"') == 1
    assert '<h1 class="shell-title">API Reference</h1>' in html
    assert 'class="shell-summary"' in html
    assert 'class="surface-nav"' in html
    assert html.index('class="shell-header"') < html.index("<redoc ")
