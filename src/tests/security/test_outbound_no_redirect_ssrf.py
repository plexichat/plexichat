"""Regression: the vetted outbound transport must never follow a redirect.

``src.utils.outbound.urlopen`` is the shared transport floor for every
configuration-controlled URL in the backend (search backends, automod AI
adapters, ...). Callers validate the *initial* URL with a host policy such as
``URLValidator``. The stock ``urllib`` opener, however, follows ``Location``
headers automatically and carries the original headers (including
``Authorization``) to the new host. That turns any validated public endpoint
that can return a 3xx into an SSRF pivot to an internal address -- and leaks
the request's auth header to the redirect target.

These tests pin the fail-closed behaviour: a 3xx is surfaced as an error and
the redirect target is never contacted.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request

import pytest

from src.utils import outbound


class _RedirectingHandler(BaseHTTPRequestHandler):
    """``/start`` 302s to ``/internal-only``; both paths record every hit."""

    hits: list[str] = []

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        type(self).hits.append(self.path)
        if self.path == "/start":
            self.send_response(302)
            self.send_header(
                "Location",
                f"http://127.0.0.1:{self.server.server_port}/internal-only",
            )
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"reached the redirect target")

    def log_message(self, *args):
        pass


@pytest.mark.security
@pytest.mark.unit
def test_vetted_transport_does_not_follow_redirect():
    """A 302 to a second local endpoint must not be followed end-to-end."""
    _RedirectingHandler.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        start_url = f"http://127.0.0.1:{server.server_port}/start"
        with pytest.raises(HTTPError) as excinfo:
            outbound.urlopen(Request(start_url), timeout=5)
        assert excinfo.value.code == 302
        assert "/start" in _RedirectingHandler.hits
        assert "/internal-only" not in _RedirectingHandler.hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.security
@pytest.mark.unit
def test_no_redirect_handler_refuses_arbitrary_target():
    """The handler itself rejects a redirect to a link-local SSRF target."""
    handler = outbound._NoRedirectHandler()
    req = Request("https://search.example/query")
    with pytest.raises(HTTPError) as excinfo:
        handler.redirect_request(
            req,
            None,
            302,
            "Found",
            {"Location": "http://169.254.169.254/latest/meta-data/"},
            "http://169.254.169.254/latest/meta-data/",
        )
    assert excinfo.value.code == 302


@pytest.mark.security
@pytest.mark.unit
def test_opener_replaced_default_redirect_handler():
    """build_opener must not silently restore the redirect-following handler."""
    assert not any(
        type(handler) is HTTPRedirectHandler for handler in outbound._OPENER.handlers
    )
    assert any(
        isinstance(handler, outbound._NoRedirectHandler)
        for handler in outbound._OPENER.handlers
    )
