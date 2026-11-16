"""SSRF hardening for attacker-supplied PlexiJoin federation endpoints.

``remote_url`` arrives in a signed join-request envelope, is stored for admin
review, and later becomes an outbound federation target. These tests pin the
gate that resolves the hostname and rejects non-global addresses *before* the
URL is persisted, and the re-check applied to every redirect hop.
"""

import socket
from unittest.mock import patch

import pytest

from src.api.routes.federation import (
    FederationEndpointRejected,
    ResolvedEndpoint,
    resolve_remote_endpoint,
    validate_redirect_chain,
)


def _addresses(*ips):
    """Build getaddrinfo-shaped results for the supplied IPv4 literals."""
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips
    ]


def test_public_https_endpoint_is_resolved_and_approved():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("93.184.216.34"),
    ):
        endpoint = resolve_remote_endpoint(
            "  https://peer.example/federate  ", require_https=True
        )

    assert isinstance(endpoint, ResolvedEndpoint)
    assert endpoint.url == "https://peer.example/federate"
    assert endpoint.hostname == "peer.example"
    # The approved IP, not the hostname, must be used for the connection.
    assert endpoint.address == "93.184.216.34"


@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata
        "https://127.0.0.1/admin",  # loopback
        "https://10.0.0.5/",  # RFC1918
        "https://100.64.0.1/",  # CGNAT
        "https://[::1]/",  # IPv6 loopback
        "https://[fd00::1]/",  # IPv6 unique-local
        "https://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
    ],
)
def test_non_global_ip_literals_are_rejected(url):
    with pytest.raises(FederationEndpointRejected):
        resolve_remote_endpoint(url)


def test_private_address_returned_by_dns_is_rejected():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("10.1.2.3"),
    ):
        with pytest.raises(FederationEndpointRejected):
            resolve_remote_endpoint("https://peer.example/")


def test_metadata_address_returned_by_dns_is_rejected():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("169.254.169.254"),
    ):
        with pytest.raises(FederationEndpointRejected):
            resolve_remote_endpoint("https://peer.example/")


def test_https_is_required_when_requested():
    with pytest.raises(FederationEndpointRejected):
        resolve_remote_endpoint("http://peer.example/", require_https=True)


def test_redirect_to_metadata_address_is_rejected():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("93.184.216.34"),
    ):
        with pytest.raises(FederationEndpointRejected):
            validate_redirect_chain(
                "https://peer.example/start",
                ["/next", "https://169.254.169.254/latest/meta-data/"],
            )


def test_dns_rebinding_on_redirect_is_rejected():
    # First lookup (the original URL) is public; the second (a redirect target)
    # flips to loopback. Re-resolving each hop is what catches this.
    answers = [
        _addresses("93.184.216.34"),
        _addresses("127.0.0.1"),
    ]
    with patch(
        "src.utils.security.socket.getaddrinfo", side_effect=answers
    ) as resolve:
        with pytest.raises(FederationEndpointRejected):
            validate_redirect_chain(
                "https://peer.example/start", ["https://peer.example/next"]
            )
    assert resolve.call_count == 2


def test_redirect_chain_accepts_all_public_hops():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("93.184.216.34"),
    ):
        endpoint = validate_redirect_chain(
            "https://peer.example/start",
            ["/next", "https://cdn.example/final"],
        )
    assert endpoint.url == "https://cdn.example/final"
    assert endpoint.address == "93.184.216.34"
