"""SSRF hardening for attacker-supplied PlexiJoin federation endpoints.

``remote_url`` arrives in a signed join-request envelope, is stored for admin
review, and later becomes an outbound federation target. These tests pin the
gate that resolves the hostname and rejects non-global addresses *before* the
URL is persisted, and the re-check applied to every redirect hop.
"""

import json
import socket
from typing import cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

from src.api.routes.federation import (
    FederationEndpointRejected,
    ResolvedEndpoint,
    _parse_json_body,
    _require_json_content_type,
    assert_tls_verification_enabled,
    build_pinned_target,
    require_secure_remote_endpoint,
    resolve_remote_endpoint,
    validate_invite_code,
    validate_invite_codes,
    validate_numeric_id,
    validate_redirect_chain,
    validate_remote_id,
    validate_type_field,
)


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    """Start every case with an empty resolution memo.

    ``URLValidator`` memoizes a validated ``(hostname, resolved_ip)`` per URL
    for a few seconds. Without this, an earlier case that approved
    ``https://peer.example/start`` leaves a hit that skips this case's
    ``getaddrinfo`` side_effect, so the *first* hop consumes the answer meant
    for the redirect and the rebinding case can never raise.
    """
    from src.utils.security import clear_url_resolution_cache

    clear_url_resolution_cache()
    yield
    clear_url_resolution_cache()


def _addresses(*ips):
    """Build getaddrinfo-shaped results for the supplied IPv4 literals."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


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
    with patch("src.utils.security.socket.getaddrinfo", side_effect=answers) as resolve:
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


# --- HTTPS-only + pinned certificate policy -------------------------------


def test_secure_endpoint_helper_rejects_plaintext():
    with pytest.raises(FederationEndpointRejected):
        require_secure_remote_endpoint("http://peer.example/")


def test_http_redirect_downgrade_is_rejected():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("93.184.216.34"),
    ):
        with pytest.raises(FederationEndpointRejected):
            validate_redirect_chain(
                "https://peer.example/start", ["http://peer.example/next"]
            )


def test_pinned_target_connects_to_approved_address_and_keeps_sni():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("93.184.216.34"),
    ):
        endpoint = require_secure_remote_endpoint("https://peer.example:8443/federate")

    target = build_pinned_target(endpoint)

    # The authority is the approved IP, never the re-resolvable hostname.
    assert "93.184.216.34" in target.request_url
    assert "peer.example" not in target.request_url
    assert target.headers["Host"] == "peer.example:8443"
    assert target.extensions["sni_hostname"] == "peer.example"
    assert target.tls_policy == "system-trust"


def test_pinned_target_refuses_plaintext():
    with patch(
        "src.utils.security.socket.getaddrinfo",
        return_value=_addresses("93.184.216.34"),
    ):
        endpoint = resolve_remote_endpoint("http://peer.example/")
    with pytest.raises(FederationEndpointRejected):
        build_pinned_target(endpoint)


def test_tls_verification_cannot_be_disabled():
    import ssl

    assert assert_tls_verification_enabled(True) is True
    assert assert_tls_verification_enabled("/etc/ssl/certs/ca.pem") is True

    for disabled in (False, None):
        with pytest.raises(FederationEndpointRejected):
            assert_tls_verification_enabled(disabled)

    unverified = ssl._create_unverified_context()
    with pytest.raises(FederationEndpointRejected):
        assert_tls_verification_enabled(unverified)


# --- Bounded payloads + content-type gate ---------------------------------


class _RequestStub:
    def __init__(self, content_type):
        self.headers = (
            {"content-type": content_type} if content_type is not None else {}
        )


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/json; charset=utf-8",
        "APPLICATION/JSON",
        "application/vnd.plexichat+json",
    ],
)
def test_json_content_types_are_accepted(content_type):
    _require_json_content_type(cast(Request, _RequestStub(content_type)))


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "application/x-www-form-urlencoded", "multipart/form-data", "", None],
)
def test_unexpected_content_types_are_rejected_before_parsing(content_type):
    with pytest.raises(HTTPException) as exc:
        _require_json_content_type(cast(Request, _RequestStub(content_type)))
    assert exc.value.status_code == 415


def test_valid_payload_parses():
    assert _parse_json_body(b'{"artifact_id": 1, "op": {"op_type": "edit"}}') == {
        "artifact_id": 1,
        "op": {"op_type": "edit"},
    }


def test_deeply_nested_payload_is_rejected():
    nested = {"x": 1}
    for _ in range(12):
        nested = {"x": nested}
    with pytest.raises(HTTPException) as exc:
        _parse_json_body(json.dumps(nested).encode())
    assert exc.value.status_code == 400


def test_wide_payload_is_rejected():
    payload = {f"k{i}": i for i in range(200)}
    with pytest.raises(HTTPException) as exc:
        _parse_json_body(json.dumps(payload).encode())
    assert exc.value.status_code == 400


def test_oversized_text_field_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _parse_json_body(json.dumps({"note": "x" * 5000}).encode())
    assert exc.value.status_code == 400


# --- Strict identifier validation ----------------------------------------


@pytest.mark.parametrize(
    "value",
    ["plexichat-1234", "abc.def:123", "local-42", "a" * 128],
)
def test_valid_remote_ids_accepted(value):
    assert validate_remote_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "has spaces",
        "bad\nnewline",
        "semi;colon",
        "a" * 129,
        123,
        None,
        "/etc/passwd",
    ],
)
def test_invalid_remote_ids_rejected(value):
    with pytest.raises(HTTPException) as exc:
        validate_remote_id(value)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("value", ["abc123", "INV-_9", "a" * 64])
def test_valid_invite_codes_accepted(value):
    assert validate_invite_code(value) == value


@pytest.mark.parametrize("value", ["", "bad code", "bad/code", "x" * 65, 12, None])
def test_invalid_invite_codes_rejected(value):
    with pytest.raises(HTTPException) as exc:
        validate_invite_code(value)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("value", [1, "990000000000000000", 2**63 - 1])
def test_valid_numeric_ids_accepted(value):
    assert validate_numeric_id(value, "artifact_id") == int(value)


@pytest.mark.parametrize(
    "value", [0, -1, "0", 2**63, "99999999999999999999", 1.5, True, "abc", None]
)
def test_invalid_numeric_ids_rejected(value):
    with pytest.raises(HTTPException) as exc:
        validate_numeric_id(value, "artifact_id")
    assert exc.value.status_code == 400


def test_actor_id_allows_zero():
    assert validate_numeric_id(0, "actor_id", minimum=0) == 0


def test_nested_invite_codes_are_validated():
    payload = {
        "message_type": "join",
        "invite_code": "valid_CODE-1",
        "nested": [{"child_invite_code": "also-valid"}, {"ok": 1}],
    }
    validate_invite_codes(payload)  # does not raise

    bad = {"nested": [{"invite_code": "bad code"}]}
    with pytest.raises(HTTPException) as exc:
        validate_invite_codes(bad)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("value", ["op.edit", "message_send", "a.b:c-1"])
def test_valid_type_fields_accepted(value):
    assert validate_type_field(value, "message_type") == value


@pytest.mark.parametrize("value", ["", "has space", "a" * 129, None, 7])
def test_invalid_type_fields_rejected(value):
    with pytest.raises(HTTPException) as exc:
        validate_type_field(value, "message_type")
    assert exc.value.status_code == 400
