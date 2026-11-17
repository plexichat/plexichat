"""Regression: a backslash in the URL authority is an SSRF parser differential.

``urlparse`` (used by the outbound gate) keeps a backslash inside the userinfo
and reports the host to the RIGHT of an ``@``. HTTP clients following the WHATWG
URL rules (``requests``/urllib3, curl, browsers) treat the backslash as a slash
and connect to the host to the LEFT of it. A URL such as

    http://169.254.169.254\\@public.example/x

therefore passes the gate against the public ``public.example`` while a sink
that fetches it by name reaches the link-local metadata endpoint. The gate must
reject such URLs before resolving DNS.
"""

from unittest.mock import patch

import pytest

from src.core.security.errors import OutboundUrlRejected
from src.core.security.outbound import parse_outbound_url, resolve_outbound_url


# Each URL is crafted so urlparse sees the harmless ``public.example`` host.
BACKSLASH_AUTHORITY_URLS = [
    r"http://169.254.169.254\@public.example/x",
    r"https://127.0.0.1\@public.example/x",
    r"http://10.0.0.5\@public.example/x",
    r"http://169.254.169.254\\@public.example/x",
    r"http://user:pass@169.254.169.254\@public.example/x",
    r"http://169.254.169.254\@public.example:8080/x?y=1#f",
]


@pytest.mark.parametrize("url", BACKSLASH_AUTHORITY_URLS)
def test_backslash_in_authority_is_rejected_before_dns(url):
    with patch("src.core.security.outbound.URLValidator") as validator:
        with pytest.raises(OutboundUrlRejected) as rejection:
            parse_outbound_url(url, "attachment url")

    # The check runs before resolution: a hostile host must never be handed to
    # the validator (and a public host must never be reported to the caller).
    validator.return_value.validate_url_for_request.assert_not_called()
    assert "attachment url" in str(rejection.value)
    assert "backslash" in str(rejection.value).lower()


@pytest.mark.parametrize("url", BACKSLASH_AUTHORITY_URLS)
def test_resolve_outbound_url_also_rejects_backslash_authority(url):
    with patch("src.core.security.outbound.URLValidator") as validator:
        with pytest.raises(OutboundUrlRejected):
            resolve_outbound_url(url, "Server icon URL")
    validator.return_value.validate_url_for_request.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        # A backslash in the path/query is harmless: it can never move the host
        # in either parser, so it is not rejected and reaches the validator.
        "http://public.example/a\\b",
        "http://public.example/a?next=\\path",
        # A percent-encoded backslash is data, not a separator; urlparse and
        # WHATWG clients agree on the host, so it is likewise not rejected.
        "http://public.example/foo%5Cbar",
        "http://169.254.169.254%5C@public.example/x",
    ],
)
def test_backslash_outside_the_authority_is_not_over_blocked(url):
    with patch("src.core.security.outbound.URLValidator") as validator:
        validator.return_value.validate_url_for_request.return_value = (
            "public.example",
            "93.184.216.34",
        )
        result = parse_outbound_url(url)

    validator.return_value.validate_url_for_request.assert_called_once_with(url)
    assert result.url == url
