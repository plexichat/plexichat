"""OpenAPI docs pages must explain a slow, failed or empty spec fetch.

Swagger UI and ReDoc both load the OpenAPI document asynchronously. Without a
status region, a failed or empty fetch leaves an empty content column with no
explanation and no way to recover.
"""

import pytest
from starlette.requests import Request

from src.api.routes.docs.openapi import (
    _build_spec_status_html,
    render_redoc_page,
    render_swagger_ui_page,
)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/docs",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )


@pytest.mark.parametrize(
    ("renderer", "widget"),
    [
        (render_swagger_ui_page, '<div id="swagger-ui">'),
        (render_redoc_page, '<redoc spec-url="/openapi.json"></redoc>'),
    ],
)
def test_loading_status_is_announced_before_the_widget(renderer, widget):
    html = renderer(_request(), "Plexichat", "/openapi.json").body.decode("utf-8")

    assert 'id="docs-spec-status"' in html
    assert 'role="status"' in html
    assert 'aria-live="polite"' in html
    assert "Loading API specification" in html
    assert 'data-spec-url="/openapi.json"' in html
    assert html.index('id="docs-spec-status"') < html.index(widget)


@pytest.mark.parametrize("renderer", [render_swagger_ui_page, render_redoc_page])
def test_failure_state_is_an_alert_with_a_retry(renderer):
    html = renderer(_request(), "Plexichat", "/openapi.json").body.decode("utf-8")

    assert 'id="docs-spec-error"' in html
    assert 'role="alert"' in html
    # Hidden until the fetch actually fails.
    assert 'id="docs-spec-error" role="alert" hidden' in html
    assert '<button type="button" onclick="location.reload()">Retry</button>' in html
    # Both failure modes are handled: bad HTTP/empty spec drops the loading
    # state and reveals the alert; a good spec clears the loading state.
    assert "response.ok" in html
    assert "the specification is empty" in html
    assert "status.hidden = true" in html
    assert "error.hidden = false" in html


def test_spec_url_in_data_attribute_is_html_escaped():
    banner = _build_spec_status_html('"><script>alert(1)</script>')

    assert 'data-spec-url="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in banner
    assert "<script>alert(1)</script>" not in banner
