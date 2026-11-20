"""Regression: hand-rolled outbound IP gates must match the central SSRF policy.

``URLValidator.is_private_ip`` explicitly blocks deprecated IPv6 site-local
(``fec0::/10``), NAT64 addresses that embed a private IPv4, CGNAT and
IPv4-mapped IPv6. Two hand-rolled gates were left behind when that policy was
centralised and relied on ``ipaddress.is_global`` instead:

* the artifact-export source URL gate (``vetted_export_addresses``), and
* the transcription provider endpoint gate (``_require_public_https_endpoint``,
  which guards a URL a Bearer API key is POSTed to).

Both accepted ``https://[fec0::1]/`` (a non-global site-local address that
``ipaddress`` does not flag as private/reserved/link-local), turning a URL-taking
caller into an SSRF primitive against internal infrastructure. The transcription
gate also accepted NAT64 literals such as ``64:ff9b::7f00:1`` (loopback behind
the well-known NAT64 prefix). These payloads must fail both gates.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from src.core.artifacts.export import UnsafeExportURL, validate_export_url
from src.core.artifacts.transcription.provider import (
    HardeningViolation,
    _require_public_https_endpoint,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]


# Concrete literals that ``ipaddress.is_global`` accepts but a real outbound
# gate must refuse. Includes site-local (fec0::/10), NAT64-embedded loopback and
# private IPv4, CGNAT, and IPv4-mapped loopback.
NON_PUBLIC_LITERALS = [
    "fec0::1",  # deprecated IPv6 site-local
    "feff::1",  # last address in fec0::/10
    "64:ff9b::7f00:1",  # NAT64 embedding 127.0.0.1
    "64:ff9b::a00:1",  # NAT64 embedding 10.0.0.1
    "::ffff:127.0.0.1",  # IPv4-mapped loopback
    "100.64.0.1",  # RFC 6598 CGNAT
]


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    from src.utils.security import clear_url_validation_cache

    clear_url_validation_cache()
    yield
    clear_url_validation_cache()


@pytest.mark.parametrize("literal", NON_PUBLIC_LITERALS)
def test_export_gate_rejects_non_public_ipv6_literals(literal):
    url = f"https://[{literal}]/export"
    with pytest.raises(UnsafeExportURL):
        validate_export_url(url)


@pytest.mark.parametrize("literal", NON_PUBLIC_LITERALS)
def test_transcription_endpoint_gate_rejects_non_public_literals(literal):
    url = f"https://[{literal}]/v1"
    with pytest.raises(HardeningViolation):
        _require_public_https_endpoint(url, "openai_api_base")


def test_export_gate_rejects_dns_answer_in_site_local_space():
    """The DNS path must apply the same IP policy as the literal path."""
    answers = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fec0::1", 443, 0, 0))]
    with patch("socket.getaddrinfo", return_value=answers):
        with pytest.raises(UnsafeExportURL):
            validate_export_url("https://renderer.example/export")


@pytest.mark.parametrize(
    "url",
    [
        "https://93.184.216.34/report.json",
        "https://example.com/report.json",
        "https://[2606:4700::1111]/report.json",
        "https://[::ffff:8.8.8.8]/report.json",
    ],
)
def test_public_targets_remain_allowed_by_both_gates(url):
    assert validate_export_url(url) == url
    assert _require_public_https_endpoint(url, "openai_api_base") == url
