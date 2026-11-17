"""SSRF regression suite for PlexiJoin federation endpoints.

Covers the three classic bypass families against ``resolve_remote_endpoint``
and ``validate_redirect_chain``:

* private/loopback/link-local/metadata/CGNAT/multicast/reserved IPs,
* DNS rebinding-style inputs (obfuscated numeric hosts, a second answer that
  flips to loopback, all-answers resolution) and IPv4-mapped/NAT64 literals,
* redirect chains, including relative targets, cross-host hops, loops and the
  hop limit — each hop is re-resolved, not trusted from the original check.
"""

import json
import socket
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from src.api.routes import federation as mod
from src.api.routes.federation import (
    FederationEndpointRejected,
    MAX_REDIRECTS,
    resolve_remote_endpoint,
    validate_redirect_chain,
)


def _addresses(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


PUBLIC = "93.184.216.34"


# --- Private / non-global addresses --------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://127.255.255.254/",
        "https://10.0.0.1/",
        "https://172.16.0.1/",
        "https://192.168.1.1/",
        "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "https://100.64.0.1/",
        "https://0.0.0.0/",
        "https://224.0.0.1/",
        "https://240.0.0.1/",
        "https://192.0.2.1/",
        "https://[::1]/",
        "https://[fc00::1]/",
        "https://[fe80::1]/",
        "https://[ff02::1]/",
    ],
)
def test_non_global_literals_are_rejected(url):
    with pytest.raises(FederationEndpointRejected):
        resolve_remote_endpoint(url, require_https=True)


def test_metadata_ip_over_plain_http_is_still_rejected():
    # The https-only rule is a separate layer; SSRF must hold on its own.
    with pytest.raises(FederationEndpointRejected):
        resolve_remote_endpoint("http://169.254.169.254/latest/meta-data/")


def test_all_resolved_answers_are_checked_not_just_the_first():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses(PUBLIC, "127.0.0.1"),
    ):
        with pytest.raises(FederationEndpointRejected):
            resolve_remote_endpoint("https://peer.example/", require_https=True)


# --- DNS rebinding-style inputs ------------------------------------------


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "0177.0.0.1"])
def test_obfuscated_ipv4_host_resolving_to_loopback_is_rejected(host):
    # A resolver may accept these integer/hex/octal spellings and hand back
    # 127.0.0.1; the resolved address, not the spelling, decides safety.
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("127.0.0.1"),
    ):
        with pytest.raises(FederationEndpointRejected):
            resolve_remote_endpoint(f"https://{host}/", require_https=True)


@pytest.mark.parametrize(
    "host",
    [
        "[::ffff:10.0.0.1]",  # IPv4-mapped loopback/RFC1918
        "[64:ff9b::a00:1]",  # NAT64 embedding 10.0.0.1
        "[::ffff:169.254.169.254]",  # IPv4-mapped metadata
    ],
)
def test_transition_addresses_are_rejected(host):
    with pytest.raises(FederationEndpointRejected):
        resolve_remote_endpoint(f"https://{host}/", require_https=True)


def test_rebinding_second_lookup_to_private_is_rejected():
    answers = [_addresses(PUBLIC), _addresses("10.0.0.1")]
    with patch("src.utils.security.socket.getaddrinfo", side_effect=answers) as resolve:
        with pytest.raises(FederationEndpointRejected):
            validate_redirect_chain(
                "https://peer.example/start", ["https://peer.example/next"]
            )
    assert resolve.call_count == 2


def test_dns_failure_is_a_rejection_not_a_pass():
    def fail(*args, **kwargs):
        raise socket.gaierror("no such host")

    with patch("src.utils.security.socket.getaddrinfo", side_effect=fail):
        with pytest.raises(FederationEndpointRejected):
            resolve_remote_endpoint("https://unknown.example/", require_https=True)


# --- Redirect chains ------------------------------------------------------


def test_relative_redirect_to_loopback_is_rejected():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses(PUBLIC),
    ):
        with pytest.raises(FederationEndpointRejected):
            validate_redirect_chain(
                "https://peer.example/start", ["//127.0.0.1/steal"]
            )


def test_cross_host_public_redirect_is_re_resolved():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses(PUBLIC),
    ) as resolve:
        endpoint = validate_redirect_chain(
            "https://peer.example/start", ["/next", "https://cdn.example/final"]
        )
    assert endpoint.url == "https://cdn.example/final"
    assert resolve.call_count == 3  # initial + two hops


def test_redirect_loop_is_rejected():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses(PUBLIC),
    ):
        with pytest.raises(FederationEndpointRejected):
            validate_redirect_chain(
                "https://peer.example/start", ["https://peer.example/start"]
            )


def test_redirect_hop_limit_is_rejected():
    locations = [f"https://peer.example/{i}" for i in range(MAX_REDIRECTS + 2)]
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses(PUBLIC),
    ):
        with pytest.raises(FederationEndpointRejected):
            validate_redirect_chain("https://peer.example/start", locations)


# --- Endpoint-level regression -------------------------------------------


@pytest.mark.asyncio
async def test_join_request_rejects_url_resolving_to_metadata_ip():
    payload = {
        "instance_id": "remote-1234",
        "remote_url": "https://peer.example/federate",
    }
    request = cast(
        Request,
        SimpleNamespace(
            headers={"content-type": "application/json"},
            body=AsyncMock(return_value=json.dumps(payload).encode()),
        ),
    )
    manager = MagicMock()
    manager.submit_inbound_request.return_value = {"id": 1}

    with patch.object(mod, "_get_plexijoin_manager", return_value=manager), patch.object(
        mod, "_authenticate", return_value={"remote_url": "https://peer.example/"}
    ), patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("169.254.169.254"),
    ):
        with pytest.raises(HTTPException) as exc:
            await mod.join_request_inbound(request)

    assert exc.value.status_code == 400
    manager.submit_inbound_request.assert_not_called()
