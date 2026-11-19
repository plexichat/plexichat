"""CGNAT must be rejected by the report-evidence and voice storage URL gates.

RFC 6598 shared address space (100.64.0.0/10) is not flagged by
``ipaddress.is_private`` on every supported Python release, and its IPv4-mapped
IPv6 spelling is not either. The project's central ``URLValidator`` treats both
as non-public. These two hand-rolled gates used to allow them, unlike every
other outbound SSRF gate, so a report evidence URL or a voice attachment URL
could point at internal CGNAT infrastructure and be stored verbatim for later
fetching/rendering.
"""

import pytest

from src.api.routes.features.reports import _is_safe_url
from src.api.routes.features.voice import _assert_public_storage_host

pytestmark = [pytest.mark.unit, pytest.mark.security]


CGNAT_LITERALS = [
    "100.64.0.1",
    "100.100.100.100",
    "100.127.255.254",
    "::ffff:100.64.0.1",
]


@pytest.mark.parametrize("host", CGNAT_LITERALS)
def test_report_evidence_gate_rejects_cgnat(host):
    bracketed = f"[{host}]" if ":" in host else host
    assert _is_safe_url(f"https://{bracketed}/collector") is False


@pytest.mark.parametrize("host", CGNAT_LITERALS)
def test_voice_storage_gate_rejects_cgnat(host):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _assert_public_storage_host(host)


def test_other_non_public_ranges_still_rejected_by_both_gates():
    from fastapi import HTTPException

    for host in ("127.0.0.1", "169.254.169.254", "10.0.0.5"):
        assert _is_safe_url(f"https://{host}/x") is False
        with pytest.raises(HTTPException):
            _assert_public_storage_host(host)


def test_public_literals_still_accepted():
    assert _is_safe_url("https://8.8.8.8/x") is True
    _assert_public_storage_host("8.8.8.8")
