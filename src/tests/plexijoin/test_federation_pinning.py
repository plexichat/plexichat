"""Outbound federation traffic must connect to the validated address.

``resolve_remote_target`` resolves a remote instance URL once and returns the
IP the SSRF check approved. Requesting the *hostname* afterwards hands the
resolution to httpx, which resolves it again — so a remote that answers with a
public address during validation can answer ``127.0.0.1`` for the health probe
or the signed POST. The media proxy, the link-preview fetcher and the webhook
deliverer all connect to the approved address; federation must too.

These tests drive the real httpx path through ``MockTransport`` and pin DNS, so
they assert the request httpx would make rather than a stub's intent.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from src.core.plexijoin import RemoteURLBlockedError

pytestmark = pytest.mark.integration

PUBLIC_IP = "93.184.216.34"
LOOPBACK = "127.0.0.1"


@pytest.fixture
def plexijoin(db):
    from src.core.plexijoin.manager import PlexiJoinManager

    class _StubEnc:
        def encrypt(self, s):
            return "enc:" + s

        def decrypt(self, s):
            return s[len("enc:") :]

    return PlexiJoinManager(db, admin_logger=None, encryption_service=_StubEnc())


def _pin_dns(monkeypatch, *addresses):
    """Answer DNS with ``addresses`` in order, repeating the last one."""
    pending = list(addresses)
    asked = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        asked.append(host)
        ip = pending.pop(0) if len(pending) > 1 else pending[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", fake_getaddrinfo)
    return asked


class _Recorder:
    """Capture every request an httpx client would put on the wire."""

    def __init__(self, status_code=200, body=None):
        self.requests = []
        self.status_code = status_code
        self.body = body if body is not None else {"ok": True}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code, json=self.body)


def _install_transport(monkeypatch, recorder):
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recorder.handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _make_connection(plexijoin, remote_url="https://remote.example"):
    return plexijoin.create_connection(
        remote_instance_id="rebind-peer",
        remote_url=remote_url,
        shared_key="sekret",
        note="test",
        admin_id=1,
    )


def test_health_probe_connects_to_the_resolved_ip(plexijoin, monkeypatch):
    asked = _pin_dns(monkeypatch, PUBLIC_IP)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)
    conn = _make_connection(plexijoin)

    result = asyncio.run(plexijoin.test_connection(conn["id"]))

    assert result["success"] is True
    assert asked == ["remote.example"], "the remote was resolved more than once"
    request = recorder.requests[0]
    assert request.url.host == PUBLIC_IP
    assert request.url.path == "/api/v1/health"
    assert request.headers["host"] == "remote.example"
    assert request.extensions.get("sni_hostname") == "remote.example"


def test_a_rebinding_answer_cannot_redirect_the_health_probe(plexijoin, monkeypatch):
    asked = _pin_dns(monkeypatch, PUBLIC_IP, LOOPBACK)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)
    conn = _make_connection(plexijoin)

    result = asyncio.run(plexijoin.test_connection(conn["id"]))

    assert result["success"] is True
    assert asked == ["remote.example"], "the endpoint was resolved more than once"
    assert recorder.requests[0].url.host == PUBLIC_IP
    assert LOOPBACK not in str(recorder.requests[0].url)


def test_signed_post_connects_to_the_resolved_ip(plexijoin, monkeypatch):
    asked = _pin_dns(monkeypatch, PUBLIC_IP)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    result = asyncio.run(
        plexijoin._post_signed(
            "https://remote.example",
            "sekret",
            "/api/v1/federation/message",
            {"content": "hi"},
        )
    )

    assert result["success"] is True
    assert asked == ["remote.example"]
    request = recorder.requests[0]
    assert request.url.host == PUBLIC_IP
    assert request.url.path == "/api/v1/federation/message"
    assert request.headers["host"] == "remote.example"
    assert request.extensions.get("sni_hostname") == "remote.example"
    # Signing headers and the body still travel untouched.
    assert request.headers["x-plexijoin-signature"]
    assert request.headers["content-type"] == "application/json"
    assert request.content == b'{"content": "hi"}'


def test_non_default_port_is_kept_in_the_request_and_host_header(
    plexijoin, monkeypatch
):
    _pin_dns(monkeypatch, PUBLIC_IP)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)
    conn = _make_connection(plexijoin, remote_url="https://remote.example:8443")

    asyncio.run(plexijoin.test_connection(conn["id"]))

    request = recorder.requests[0]
    assert request.url.host == PUBLIC_IP
    assert request.url.port == 8443
    assert request.headers["host"] == "remote.example:8443"


def test_a_private_resolution_is_still_refused(plexijoin, monkeypatch):
    _pin_dns(monkeypatch, LOOPBACK)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)
    conn = _make_connection(plexijoin)

    result = asyncio.run(plexijoin.test_connection(conn["id"]))

    assert result["success"] is False
    assert "blocked" in result["error"]
    assert recorder.requests == []


def test_signed_post_refuses_a_private_resolution(plexijoin, monkeypatch):
    _pin_dns(monkeypatch, LOOPBACK)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    with pytest.raises(RemoteURLBlockedError):
        asyncio.run(
            plexijoin._post_signed(
                "https://remote.example",
                "sekret",
                "/api/v1/federation/message",
                {"content": "hi"},
            )
        )
    assert recorder.requests == []
