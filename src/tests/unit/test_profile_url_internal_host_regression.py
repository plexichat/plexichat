"""Regression: profile links must reject every internal IPv4 spelling.

The profile URL guard used to call ``ipaddress.ip_address`` on the host string
alone. That parses canonical dotted quads and IPv6 but not the numeric/shorthand
forms browsers also treat as IPv4 literals (``2130706433``, ``0x7f000001``,
``127.1`` ...). A stored social link or banner could therefore point a viewer's
browser at their own loopback or local network while passing the guard.
"""

import pytest

from src.core.profiles.manager import _validate_profile_url
from src.core.security.errors import OutboundUrlRejected


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.5",
        "169.254.169.254",
        "2130706433",  # decimal loopback
        "0x7f000001",  # hex loopback
        "0177.0.0.1",  # octal loopback
        "127.1",  # shorthand loopback
        "127.0.1",  # shorthand loopback, three octets
        "[::1]",
        "localhost",
        "localhost.",  # trailing dot must not slip past the name check
        "router.local",
    ],
)
def test_internal_literal_forms_rejected(host):
    with pytest.raises(OutboundUrlRejected):
        _validate_profile_url(f"http://{host}/x", "banner_url")


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.com/a.png",
        "https://example.com/path?q=1",
        "https://8.8.8.8/x",
    ],
)
def test_public_links_still_allowed(url):
    assert _validate_profile_url(url, "banner_url") == url
