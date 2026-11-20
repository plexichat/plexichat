"""Regression: deprecated IPv6 site-local space must fail the SSRF gate.

``ipaddress`` does not flag ``fec0::/10`` (RFC 3513 site-local) as private,
reserved or link-local, so a literal or a DNS answer in that range used to pass
``URLValidator.is_private_ip`` and every gate built on it -- even though it is
non-global and routes only inside a site. That let a caller turn any URL-taking
endpoint (report evidence links, outbound webhooks, embed URLs, ...) into an
SSRF primitive against internal infrastructure. The fix blocks the narrow
``fec0::/10`` range explicitly without touching genuinely public IPv6 space.
"""

import ipaddress
from unittest.mock import patch

import pytest

from src.utils.security import URLValidator

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    # A short-TTL resolution memo may be shared across tests in one process;
    # start each case with a clean slate so the gate is actually exercised.
    try:
        from src.utils.security import clear_url_validation_cache

        clear_url_validation_cache()
    except Exception:
        pass
    yield


SITE_LOCAL = [
    "fec0::1",
    "fec0:ffff:ffff:ffff:ffff:ffff:ffff:ffff",  # last address in fec0::/10
    "feff::1",  # still inside fec0::/10
]

PUBLIC = [
    "2001:4860:4860::8888",
    "2606:4700::1111",
    "2620:fe::fe",
    "::ffff:8.8.8.8",
]


@pytest.mark.parametrize("ip", SITE_LOCAL)
def test_is_private_ip_blocks_site_local(ip):
    assert ipaddress.ip_address(ip) in ipaddress.ip_network("fec0::/10")
    assert URLValidator().is_private_ip(ip) is True


@pytest.mark.parametrize("ip", PUBLIC)
def test_is_private_ip_still_allows_public_ipv6(ip):
    assert URLValidator().is_private_ip(ip) is False


def test_validate_url_for_request_rejects_site_local_literal():
    # A bracketed IPv6 literal host resolves to itself; the gate must refuse it
    # before any connection is made.
    with pytest.raises(ValueError):
        URLValidator().validate_url_for_request("https://[fec0::1]/")


def test_validate_url_for_request_rejects_hostname_resolving_to_site_local():
    # A name under the attacker's control can answer with a site-local address;
    # every resolved address is checked, not just the first.
    addr_info = [
        (10, 6, 17, "", ("fec0::1", 443, 0, 0)),
    ]
    with patch("src.utils.security.socket.getaddrinfo", return_value=addr_info):
        with pytest.raises(ValueError):
            URLValidator().validate_url_for_request("https://internal.example/")


def test_outbound_gate_rejects_site_local_literal():
    # The single choke point used by profile links, webhooks, evidence URLs...
    # An IP literal needs no DNS, so this exercises the real gate end-to-end.
    from src.core.security.outbound import parse_outbound_url

    with pytest.raises(ValueError):
        parse_outbound_url("https://[fec0::1]/", "url")


def test_report_evidence_url_rejects_site_local_literal():
    # Report submission delegates its IP policy to the central gate.
    from src.api.routes.features.reports import _is_safe_url

    assert _is_safe_url("https://[fec0::1]/evidence") is False
