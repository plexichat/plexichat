"""A PlexiJoin remote URL must not carry embedded credentials.

``resolve_remote_target`` used to hand a caller-supplied URL straight to
``URLValidator`` after only a scheme check. Unlike the shared outbound gate
(``src.core.security.outbound.parse_outbound_url``), it never rejected
``user:pass@host`` credentials. That matters because the remote URL is stored
verbatim and echoed back by ``/join/list`` (``_serialize_join``), is folded into
``remote_instance_id`` (``urlparse(url).netloc``), and is re-sent on every
outbound delivery -- so a single stored value leaked the secret to every admin
with read access and to logs, and replayed it to the remote on each request.

The check must happen before DNS resolution so a rejected URL cannot be used as
a resolution oracle either.
"""

from __future__ import annotations

import socket

import pytest

from src.core.plexijoin import RemoteURLBlockedError, validate_remote_url
from src.core.plexijoin.manager import resolve_remote_target

pytestmark = pytest.mark.unit

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _pin_public_dns(monkeypatch):
    """Answer any DNS lookup with a public address; never touch the network."""
    asked = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        asked.append(host)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, 0))
        ]

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", fake_getaddrinfo)
    return asked


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.com/join",
        "https://user@example.com/join",
        "https://:pass@example.com/join",
        "https://user:pass@example.com:443/join",
    ],
)
def test_embedded_credentials_rejected(url):
    with pytest.raises(RemoteURLBlockedError, match="credentials"):
        validate_remote_url(url)


def test_credentials_rejected_before_dns(_pin_public_dns):
    """A rejected URL must not reach DNS (no resolution oracle / no work)."""
    with pytest.raises(RemoteURLBlockedError):
        resolve_remote_target("https://user:pass@example.com/join")
    assert _pin_public_dns == []


def test_clean_https_url_still_accepted(_pin_public_dns):
    """Non-vacuity: a normal URL continues to validate and resolve."""
    hostname, address = resolve_remote_target("https://example.com/join")
    assert hostname == "example.com"
    assert address == PUBLIC_IP
    assert _pin_public_dns == ["example.com"]
