"""Regression: backslash-in-authority SSRF parser differential in Web Push.

``urlparse`` -- used by ``require_https_url`` and ``_require_public_endpoint``
-- treats a literal ``\\`` as an ordinary character inside a URL authority.
``urllib3``/``requests`` (pywebpush's HTTP client) follow the WHATWG rule and
read ``\\`` as ``/`` in an http(s) URL. That disagreement lets a subscription
endpoint such as::

    https://169.254.169.254\\push.example/ep

be validated as the public-looking host ``169.254.169.254\\push.example`` while
the transport connects to the cloud metadata address to the left of the
backslash. The endpoint gate must refuse a literal backslash in the authority
before doing any host matching.

Percent-encoded ``%5C`` is *not* affected: both parsers keep it inside the
host/path text, so those URLs must remain allowed.
"""

from __future__ import annotations

import pytest

from src.core.push.manager.web_push import _require_public_endpoint
from src.core.security.hardening import HardeningViolation, require_https_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://169.254.169.254\\push.example/ep",
        "https://127.0.0.1\\push.example/ep",
        "https://[::1]\\push.example/ep",
        "https://metadata\\push.example/ep",
        "https://169.254.169.254\\@push.example/ep",
        "https://push.example\\169.254.169.254/ep",
        "https://user:pass@169.254.169.254\\push.example/ep",
    ],
)
def test_literal_backslash_in_authority_is_refused(endpoint):
    # The full registration/delivery order: require_https_url runs first, then
    # the public-endpoint gate. A credential-bearing variant may already fail
    # the former; either way it must never be admitted as a push endpoint.
    with pytest.raises((ValueError, HardeningViolation)):
        endpoint = require_https_url(endpoint, "Web Push endpoint")
        _require_public_endpoint(endpoint)


def test_literal_backslash_is_refused_even_after_https_check():
    # Non-credential form passes require_https_url (it is a well-formed HTTPS
    # URL with a host), so the rejection must come from the endpoint gate.
    endpoint = require_https_url(
        "https://169.254.169.254\\push.example/ep", "Web Push endpoint"
    )
    with pytest.raises(ValueError, match="backslash"):
        _require_public_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        # Percent-encoded backslash stays in the host text for both parsers;
        # it cannot influence which address urllib3 connects to.
        "https://169.254.169.254%5Cpush.example/ep",
        # A backslash in the path is data, not authority, and is harmless.
        "https://push.example/ep\\something",
        # Ordinary public endpoints remain allowed.
        "https://fcm.googleapis.com/fcm/send/abc",
    ],
)
def test_non_authority_backslashes_and_public_endpoints_remain_allowed(endpoint):
    endpoint = require_https_url(endpoint, "Web Push endpoint")
    assert _require_public_endpoint(endpoint) == endpoint
