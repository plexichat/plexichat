"""DNS-rebinding defence for outbound webhook deliveries.

``URLValidator`` resolves an endpoint hostname once and returns the address
it approved. If the delivery engine then POSTs the original URL, the HTTP
client resolves the name a *second* time — and a domain that answered with a
public address during validation can answer ``127.0.0.1`` (or any internal
target) for the connection. That is the rebinding attack the validator exists
to stop, and the media proxy and link-preview fetchers already avoid it by
connecting to the resolved IP.

These tests pin the same contract for webhooks:

* the request goes to the validated IP, once, with no re-resolution;
* the real hostname travels in ``Host`` (bracketed, non-default port kept);
* HTTPS keeps the hostname as the TLS SNI / certificate name, or every
  certificate check fails against a bare IP;
* the loopback testing seam (``allow_private_network``) still posts the
  literal URL to the local server a test bound.
"""

import socket

import pytest

from src.core.webhooks.delivery import (
    WebhookDeliverer,
    WebhookDeliveryError,
    pinned_request,
)

# Publicly routable addresses the validator accepts.
PUBLIC_IP = "93.184.216.34"  # 2**53 is irrelevant here; these are IPs
PUBLIC_IPV6 = "2606:4700::1111"
LOOPBACK = "127.0.0.1"


class _Response:
    def __init__(self, status_code=200):
        self.status_code = status_code


class _Recorder:
    """Fake HTTP client recording the exact request it was asked to make."""

    def __init__(self, status_code=200):
        self.status_code = status_code
        self.calls = []

    def post(self, url, json=None, headers=None, extensions=None):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": dict(headers or {}),
                "extensions": extensions,
            }
        )
        return _Response(self.status_code)


def _record(url):
    return {"endpoint_url": url, "payload": {"a": 1}, "webhook_id": 1}


def _resolve_to(monkeypatch, *addresses):
    """Answer DNS with ``addresses`` in order, repeating the last one.

    Returns the list of hostnames that were resolved, so a test can prove the
    engine asked only once.
    """
    pending = list(addresses)
    asked = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        asked.append(host)
        ip = pending.pop(0) if len(pending) > 1 else pending[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", fake_getaddrinfo)
    return asked


def test_delivery_posts_to_the_validated_address(monkeypatch):
    asked = _resolve_to(monkeypatch, PUBLIC_IP)
    client = _Recorder()

    assert (
        WebhookDeliverer(client=client).deliver(_record("https://hooks.example.com/e"))
        == 200
    )

    assert len(asked) == 1
    call = client.calls[0]
    assert call["url"] == f"https://{PUBLIC_IP}:443/e"
    assert call["headers"]["Host"] == "hooks.example.com"
    assert call["extensions"] == {"sni_hostname": "hooks.example.com"}


def test_a_rebinding_answer_cannot_redirect_the_connection(monkeypatch):
    """The regression this file exists for.

    The first answer (validation) is public; a second lookup would return
    loopback. Delivery must succeed against the public address and must never
    ask the resolver again.
    """
    asked = _resolve_to(monkeypatch, PUBLIC_IP, LOOPBACK)
    client = _Recorder()

    assert (
        WebhookDeliverer(client=client).deliver(_record("https://hooks.example.com/e"))
        == 200
    )

    assert asked == ["hooks.example.com"], "the endpoint was resolved more than once"
    assert client.calls[0]["url"].startswith(f"https://{PUBLIC_IP}:443/")
    assert LOOPBACK not in client.calls[0]["url"]
    assert LOOPBACK not in client.calls[0]["headers"]["Host"]


def test_host_header_keeps_a_non_default_port(monkeypatch):
    _resolve_to(monkeypatch, PUBLIC_IP)
    client = _Recorder()

    WebhookDeliverer(client=client).deliver(_record("https://hooks.example.com:8443/e"))

    call = client.calls[0]
    assert call["url"] == f"https://{PUBLIC_IP}:8443/e"
    assert call["headers"]["Host"] == "hooks.example.com:8443"


def test_http_endpoint_sends_no_sni_extension(monkeypatch):
    _resolve_to(monkeypatch, PUBLIC_IP)
    client = _Recorder()

    WebhookDeliverer(client=client).deliver(_record("http://hooks.example.com/e"))

    call = client.calls[0]
    assert call["url"] == f"http://{PUBLIC_IP}:80/e"
    assert call["headers"]["Host"] == "hooks.example.com"
    # The client is called without an `extensions` argument at all.
    assert call["extensions"] is None


def test_query_and_path_survive_the_rebuild(monkeypatch):
    _resolve_to(monkeypatch, PUBLIC_IP)
    client = _Recorder()

    WebhookDeliverer(client=client).deliver(
        _record("https://hooks.example.com/a/b?token=abc&n=1#frag")
    )

    assert client.calls[0]["url"] == f"https://{PUBLIC_IP}:443/a/b?token=abc&n=1"


def test_userinfo_is_never_forwarded(monkeypatch):
    """A stored ``user:pass@`` must not be replayed to the endpoint."""
    _resolve_to(monkeypatch, PUBLIC_IP)
    client = _Recorder()

    WebhookDeliverer(client=client).deliver(
        _record("https://user:secret@hooks.example.com/e")
    )

    call = client.calls[0]
    assert "user" not in call["url"]
    assert "secret" not in call["url"]
    assert call["headers"]["Host"] == "hooks.example.com"


def test_ipv6_resolved_address_is_bracketed(monkeypatch):
    _resolve_to(monkeypatch, PUBLIC_IPV6)
    client = _Recorder()

    WebhookDeliverer(client=client).deliver(_record("https://hooks.example.com/e"))

    assert client.calls[0]["url"] == f"https://[{PUBLIC_IPV6}]:443/e"
    assert client.calls[0]["headers"]["Host"] == "hooks.example.com"


def test_loopback_test_seam_posts_the_literal_url(monkeypatch):
    """``allow_private_network`` must keep the local-server e2e tests working."""
    asked = _resolve_to(monkeypatch, LOOPBACK)
    client = _Recorder()

    WebhookDeliverer(client=client, allow_private_network=True).deliver(
        _record("http://127.0.0.1:8123/hooks/x")
    )

    assert client.calls[0]["url"] == "http://127.0.0.1:8123/hooks/x"
    assert asked == [], "the test seam must not resolve anything"


def test_pinned_request_rejects_an_invalid_port():
    with pytest.raises(WebhookDeliveryError, match="port"):
        pinned_request(
            "https://hooks.example.com:99999999/e", "hooks.example.com", PUBLIC_IP
        )
