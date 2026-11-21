"""Regression: webhook delivery endpoints must not embed credentials.

``validate_delivery_endpoint`` is the gate every outbound webhook endpoint
passes through (at enqueue and again at delivery). The sibling outbound gate
``src.core.security.outbound.parse_outbound_url`` already refuses
``user:pass@host`` before DNS resolution; the webhook gate used to accept it
and relied on ``pinned_request_target`` stripping the userinfo at the sink.
That let a credential-bearing URL be persisted in the retry queue and echoed
in logs/errors. The rule is now owned by the gate itself.

The rejection cases need no network at all (the check runs before DNS), and
the success cases clear/mock the resolution cache so this file stays
independent of the DNS-cache test-isolation failures in the other webhook
delivery tests.
"""

import socket

import pytest

from src.core.webhooks.url_safety import validate_delivery_endpoint
from src.utils.security import clear_url_validation_cache

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _isolate_url_resolution_cache():
    clear_url_validation_cache()
    yield
    clear_url_validation_cache()


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@hooks.example.com/e",
        "http://user:secret@hooks.example.com/e",
        "https://user@hooks.example.com/e",
        "https://:secret@hooks.example.com/e",
        "https://user:secret@hooks.example.com:8443/e",
    ],
)
def test_embedded_credentials_rejected(url):
    with pytest.raises(ValueError, match="credentials"):
        validate_delivery_endpoint(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:secret@127.0.0.1:9/hook",
        "http://user@127.0.0.1:9/hook",
        "http://:secret@127.0.0.1:9/hook",
    ],
)
def test_embedded_credentials_rejected_even_on_loopback_test_seam(url):
    # The testing seam skips DNS, but it must not skip the structural check.
    with pytest.raises(ValueError, match="credentials"):
        validate_delivery_endpoint(url, allow_private_network=True)


def test_legitimate_loopback_endpoint_still_validates():
    assert validate_delivery_endpoint(
        "http://127.0.0.1:9/hook", allow_private_network=True
    ) == ("127.0.0.1", "")


def test_legitimate_public_endpoint_still_validates(monkeypatch):
    asked = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        asked.append(host)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, 0))
        ]

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", fake_getaddrinfo)
    assert validate_delivery_endpoint("https://hooks.example.com/e") == (
        "hooks.example.com",
        PUBLIC_IP,
    )
    assert asked == ["hooks.example.com"]
