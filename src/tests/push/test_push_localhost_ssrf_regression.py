"""Regression: ``*.localhost`` Web Push endpoints are loopback SSRF targets.

RFC 6761 guarantees that every name ending in ``.localhost`` resolves to the
loopback address, and ``.localdomain`` is the conventional loopback alias.
Neither is a literal IP, so the numeric-address gate cannot catch them. An
authenticated caller who registers a subscription with an endpoint such as
``https://anything.localhost/path`` would otherwise have pywebpush POST the
push payload straight back to the server itself. The endpoint gate must refuse
these names just as it refuses ``localhost`` and ``127.0.0.1``.
"""

from __future__ import annotations

import pytest

from src.core.push.manager.web_push import _require_public_endpoint

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://anything.localhost/path",
        "https://foo.bar.localhost/path",
        "https://anything.localhost./path",  # trailing dot must not fool it
        "https://ANYTHING.LOCALHOST/path",  # host is lowercased before matching
        "https://host.localdomain/path",
        "https://ip6-localhost/path",
        "https://ip6-loopback/path",
        "https://loopback/path",
    ],
)
def test_loopback_resolving_dns_names_are_refused(endpoint):
    with pytest.raises(ValueError, match="internal host"):
        _require_public_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://push.example/ep",
        "https://localhost.example.org/ep",  # real public DNS name
        "https://fcm.googleapis.com/fcm/send/abc",
    ],
)
def test_public_endpoints_remain_allowed(endpoint):
    assert _require_public_endpoint(endpoint) == endpoint
