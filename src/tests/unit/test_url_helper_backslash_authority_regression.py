"""Regression: shared URL shape helpers must reject a backslash in the authority.

``urlparse`` treats a literal ``\\`` as an ordinary character inside a URL
authority, so ``urlparse(url).hostname`` reports the host to the RIGHT of an
``@``. Browsers, curl and the HTTP clients downstream of these helpers follow
the WHATWG rule for http(s)/ws(s) and read the backslash as ``/``, connecting
to the host on the LEFT instead. That parser differential lets a value that
passed the shape gate resolve to a different (possibly internal) host for any
caller that does not add its own SSRF host gate.

``outbound.parse_outbound_url`` and ``validate_http_url`` already reject this;
the lower-level ``require_http_url`` / ``require_https_url`` / ``require_ws_url``
helpers did not, leaving OAuth redirect URIs, webhook endpoints, interaction
endpoints, activity URLs, voice signaling URLs and APNs targets exposed when
called through the core boundary. The refusal must happen in the shared helper.
"""

import pytest

from src.core.security.hardening import (
    HardeningViolation,
    require_http_url,
    require_https_url,
    require_ws_url,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]


# Each of these is a well-formed http(s)/ws(s) URL with NO embedded credentials
# per urlparse, so only an authority-backslash check can reject them.
HTTP_BACKSLASH_AUTHORITY = [
    "https://169.254.169.254\\@public.example/latest/meta-data/",
    "https://127.0.0.1\\@public.example/x",
    "https://10.0.0.5\\@public.example/x",
    "https://metadata\\public.example/x",
    "https://8.8.8.8\\public.example/x",
    "https://public.example\\@evil.test/x",
]

WS_BACKSLASH_AUTHORITY = [
    "wss://169.254.169.254\\@public.example/socket",
    "ws://127.0.0.1\\@public.example/socket",
    "wss://relay.example\\@evil.test/socket",
]


@pytest.mark.parametrize("helper", (require_http_url, require_https_url))
@pytest.mark.parametrize("url", HTTP_BACKSLASH_AUTHORITY)
def test_http_shape_helpers_reject_backslash_authority(helper, url):
    with pytest.raises(HardeningViolation, match="backslash"):
        helper(url, "endpoint")


@pytest.mark.parametrize("url", WS_BACKSLASH_AUTHORITY)
def test_ws_shape_helper_rejects_backslash_authority(url):
    with pytest.raises(HardeningViolation, match="backslash"):
        require_ws_url(url, "signaling endpoint")


def test_rejection_reasons_are_field_specific():
    with pytest.raises(HardeningViolation, match="remote_url"):
        require_https_url("https://8.8.8.8\\public.example/x", "remote_url")
    with pytest.raises(HardeningViolation, match="APNs URL"):
        require_https_url("https://8.8.8.8\\public.example/x", "APNs URL")


@pytest.mark.parametrize(
    "url",
    [
        "https://8.8.8.8/x",
        "https://8.8.8.8/path?q=one%5Ctwo",  # %5C is data, not a backslash
        "https://8.8.8.8/ep\\something",  # backslash after the authority
        "wss://relay.test/sock\\path",
    ],
)
def test_non_authority_backslashes_and_plain_urls_still_pass(url):
    helper = require_ws_url if url.startswith("ws") else require_http_url
    assert helper(url, "endpoint") == url


@pytest.mark.parametrize(
    "helper", (require_http_url, require_https_url, require_ws_url)
)
def test_credentialed_authority_still_rejected(helper):
    # The new check must not replace the existing no-credentials rule.
    scheme = "wss" if helper is require_ws_url else "https"
    with pytest.raises(HardeningViolation, match="credentials"):
        helper(f"{scheme}://user:pass@example.example/x", "endpoint")
