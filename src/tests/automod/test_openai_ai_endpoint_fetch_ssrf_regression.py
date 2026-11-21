"""Regression: the OpenAI-moderation *fetch* must screen the target IP.

``automod.ai.openai.api_url`` is operator/admin config. Pre-fix,
``OpenAIAdapter.check_content`` handed it straight to ``src.utils.outbound.urlopen``.
That transport enforces the scheme allowlist and refuses redirects, but does not
look at the resolved IP at all: a loopback, link-local or private address (or a
hostname answering private to the fetch-time resolver after passing the
write-time admin-schema check) was connected to and received the configured
API key. The schema gate is a write boundary only -- it neither pins the address
nor covers config loaded straight from disk -- so the fetch site itself has to
fail closed. The adapter's https-only rule is unchanged.

All network effects below are fake resolver / fake transport doubles: no real
sockets and no real DNS. The first test pins the pre-fix behaviour of the raw
transport; the rest pin the post-fix behaviour of the guarded fetch site.
"""

import socket
from urllib.request import Request

import pytest

from src.core.automod.ai import openai as openai_mod
from src.core.automod.ai.openai import OpenAIAdapter

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _Resp:
    """Minimal context-managed response used by both fake transports."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return (
            b'{"results": [{"flagged": false, "categories": {}, '
            b'"category_scores": {}}]}'
        )


def _fake_pinned_transport(monkeypatch):
    """Replace the guarded fetch site's pinned transport with a recorder."""
    calls = []

    def fake(url, *, timeout=30, method="GET", data=None, headers=None):
        calls.append((url, method, timeout, dict(headers or {})))
        return _Resp()

    monkeypatch.setattr(openai_mod, "_pinned_urlopen", fake)
    return calls


def test_raw_transport_still_fetches_private_without_an_ip_check(monkeypatch):
    """Pre-fix proof: ``outbound.urlopen`` has no IP-range screening.

    This is the transport ``OpenAIAdapter`` called before the fix. With a fake
    opener underneath, a loopback URL reaches it unchallenged, confirming the
    missing check was at the fetch site rather than in the scheme gate.
    """
    from src.utils import outbound

    seen = []

    class _FakeOpener:
        def open(self, request, timeout=None):
            seen.append((request.full_url, timeout))
            return _Resp()

    monkeypatch.setattr(outbound, "_OPENER", _FakeOpener())
    with outbound.urlopen(Request("https://127.0.0.1:8443/v1/moderations"), timeout=7):
        pass

    assert seen and seen[0][0] == "https://127.0.0.1:8443/v1/moderations"
    assert seen[0][1] == 7


def test_loopback_literal_endpoint_is_denied_before_the_fetch(monkeypatch):
    calls = _fake_pinned_transport(monkeypatch)
    adapter = OpenAIAdapter(
        {"api_key": "s3cr3t", "api_url": "https://127.0.0.1:8443/v1/moderations"}
    )

    with pytest.raises(openai_mod.AIBackendError):
        adapter.check_content("hello")

    assert calls == [], "loopback endpoint reached the transport"


def test_hostname_resolving_to_private_ip_is_denied(monkeypatch):
    calls = _fake_pinned_transport(monkeypatch)
    # Fake DNS answer: public-looking name, private address. The real
    # URLValidator still screens every answer for non-public ranges.
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))
        ],
    )
    adapter = OpenAIAdapter(
        {"api_key": "s3cr3t", "api_url": "https://rebind.example/v1/moderations"}
    )

    with pytest.raises(openai_mod.AIBackendError):
        adapter.check_content("hello")

    assert calls == [], "private-resolving endpoint reached the transport"


def test_resolution_failure_fails_closed(monkeypatch):
    calls = _fake_pinned_transport(monkeypatch)

    def boom(host, port):
        raise socket.gaierror("DNS resolution failed for rebind.example")

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", boom)
    adapter = OpenAIAdapter(
        {"api_key": "s3cr3t", "api_url": "https://rebind.example/v1/moderations"}
    )

    with pytest.raises(openai_mod.AIBackendError):
        adapter.check_content("hello")

    assert calls == [], "unresolvable endpoint reached the transport"


def test_public_endpoint_is_still_fetched_with_the_key(monkeypatch):
    """Control: a legitimate public URL is fetched with the API key intact."""
    calls = _fake_pinned_transport(monkeypatch)
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))
        ],
    )
    adapter = OpenAIAdapter(
        {"api_key": "s3cr3t", "api_url": "https://moderation.example/v1/moderations"}
    )

    result = adapter.check_content("hello")

    assert result.flagged is False
    assert len(calls) == 1
    url, method, timeout, headers = calls[0]
    assert url == "https://moderation.example/v1/moderations"
    assert method == "POST"
    assert timeout == 10.0
    assert headers.get("Authorization") == "Bearer s3cr3t"
