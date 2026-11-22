"""Focused audit proof: outbound webhook target URL SSRF gate.

Non-vacuous checks that the create/enqueue gate, the delivery-time re-check,
redirect handling, and DNS-rebinding pinning all refuse internal targets and
non-http(s) schemes. Run with ``pytest -n 0``.
"""

import socket

import pytest

from src.core.webhooks.url_safety import (
    resolve_redirect_target,
    validate_delivery_endpoint,
)
from src.core.webhooks.delivery import WebhookDeliveryError, WebhookDeliverer
from src.utils.security import clear_url_resolution_cache

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _fresh_resolution_cache():
    # The SSRF validator memoizes successful resolutions process-wide with a
    # short TTL. Clear it so each test exercises a real (mocked) DNS lookup
    # instead of a previous test's cached "public" answer.
    clear_url_resolution_cache()
    yield
    clear_url_resolution_cache()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/hook",
        "http://127.0.0.1/hook",
        "http://[::1]/hook",
        "http://169.254.169.254/latest/meta-data/",  # link-local metadata
        "http://10.0.0.5/hook",
        "http://192.168.1.1/hook",
        "http://100.64.0.1/hook",  # CGNAT
        "file:///etc/passwd",
        "ftp://example.com/hook",
        "gopher://example.com/hook",
    ],
)
def test_enqueue_gate_rejects_internal_or_non_http_targets(url):
    with pytest.raises(ValueError):
        validate_delivery_endpoint(url)


def test_gate_resolves_then_pins_public_address(monkeypatch):
    asked = []

    def fake_getaddrinfo(host, port, *a, **kw):
        asked.append(host)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (PUBLIC_IP, 0),
            )
        ]

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", fake_getaddrinfo)
    hostname, resolved_ip = validate_delivery_endpoint("https://hooks.example.com/e")
    assert hostname == "hooks.example.com"
    assert resolved_ip == PUBLIC_IP
    assert asked == ["hooks.example.com"]


def test_gate_rejects_hostname_that_resolves_to_metadata(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **kw):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("169.254.169.254", 0),
            )
        ]

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="forbidden"):
        validate_delivery_endpoint("https://hooks.example.com/e")


def test_redirect_to_internal_target_is_refused():
    with pytest.raises(ValueError):
        resolve_redirect_target(
            "https://hooks.example.com/e",
            "http://169.254.169.254/latest/meta-data/",
        )


class _Response:
    def __init__(self, status_code=200):
        self.status_code = status_code


class _Recorder:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, extensions=None):
        self.calls.append(url)
        return _Response(200)


def test_delivery_pins_validated_ip_and_never_reresolves(monkeypatch):
    asked = []

    def fake_getaddrinfo(host, port, *a, **kw):
        asked.append(host)
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (PUBLIC_IP, 0),
            )
        ]

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", fake_getaddrinfo)
    client = _Recorder()
    WebhookDeliverer(client=client).deliver(
        {"endpoint_url": "https://hooks.example.com/e", "payload": {"a": 1}}
    )
    assert asked == ["hooks.example.com"]
    assert client.calls == [f"https://{PUBLIC_IP}:443/e"]


def test_delivery_refuses_private_endpoint_at_delivery_time():
    client = _Recorder()
    with pytest.raises(WebhookDeliveryError, match="SSRF validation"):
        WebhookDeliverer(client=client).deliver(
            {
                "endpoint_url": "http://169.254.169.254/latest/meta-data/",
                "payload": {"a": 1},
            }
        )
    assert client.calls == []
