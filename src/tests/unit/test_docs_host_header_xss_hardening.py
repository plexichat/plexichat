"""Docs runtime URLs must not reflect a hostile Host header into HTML.

``_runtime_docs_config`` builds the REST/gateway URLs shown on every docs page
from the request's ``Host`` header. Those URLs are interpolated into raw HTML
in two places (the footer runtime pills and the ``{{BASE_URL}}``/``{{WS_URL}}``
markdown placeholders, which mistune renders with ``escape=False``). A client
that can set an arbitrary Host value could therefore inject markup/script.

The fix validates the host at the source and escapes at both sinks. These
tests pin all three layers and confirm legitimate hosts still work.
"""

from types import SimpleNamespace

from src.api.routes.docs.config import (
    DocsConfig,
    _runtime_docs_config,
    get_docs_config,
)
from src.api.routes.docs.navigation import build_runtime_pills_html
from src.api.routes.docs.renderer import _replace_dynamic_placeholders


HOSTILE_HOST = 'evil.example/"><img src=x onerror=alert(1)>'
INJECTION_MARKER = "<img src=x onerror=alert(1)>"


def _request(host: str, scheme: str = "https"):
    """Minimal stand-in for a Request; only headers/url are read."""
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda key, default=None: host),
        url=SimpleNamespace(scheme=scheme),
    )


def test_runtime_config_sanitizes_hostile_host():
    conf = _runtime_docs_config(_request(HOSTILE_HOST), get_docs_config())

    assert INJECTION_MARKER not in conf.base_url
    assert INJECTION_MARKER not in conf.websocket_url
    assert "<" not in conf.base_url and ">" not in conf.base_url
    # Falls back to a neutral host, still producing usable URLs.
    assert conf.base_url.startswith("https://localhost")
    assert conf.websocket_url.startswith("wss://localhost")


def test_runtime_config_keeps_legitimate_hosts():
    conf = _runtime_docs_config(_request("docs.example.com:8443"), get_docs_config())

    assert "docs.example.com:8443" in conf.base_url
    assert "docs.example.com:8443" in conf.websocket_url


def test_runtime_config_rejects_nonstandard_scheme():
    conf = _runtime_docs_config(_request("docs.example.com", scheme="javascript"), get_docs_config())

    assert conf.base_url.startswith("http://")
    assert conf.websocket_url.startswith("ws://")


def test_footer_pills_escape_hostile_config_values():
    conf = DocsConfig(base_url=HOSTILE_HOST, websocket_url=HOSTILE_HOST)

    html = build_runtime_pills_html(conf)

    assert INJECTION_MARKER not in html
    assert "&lt;img" in html
    assert "&quot;" in html


def test_markdown_placeholders_escape_hostile_config_values():
    conf = DocsConfig(base_url=HOSTILE_HOST, websocket_url=HOSTILE_HOST)

    rendered = _replace_dynamic_placeholders(
        "{{BASE_URL}} {{API_BASE_URL}} {{WEBSOCKET_URL}} {{WS_URL}}", conf
    )

    assert INJECTION_MARKER not in rendered
    assert "&lt;img" in rendered


def test_docs_page_does_not_reflect_hostile_host(test_client):
    """End-to-end: a hostile Host cannot inject markup into the Swagger page."""
    response = test_client.get("/docs", headers={"host": HOSTILE_HOST})

    assert response.status_code == 200
    assert INJECTION_MARKER not in response.text
