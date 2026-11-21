"""Regression: the AutoMod custom-endpoint *fetch* must screen the target IP.

``automod.ai.custom.endpoint_url`` is operator/admin config. Pre-fix,
``CustomAdapter.check_content`` handed it straight to ``src.utils.outbound.urlopen``.
That transport enforces the scheme allowlist and refuses redirects, but it does
not look at the resolved IP at all: a loopback, link-local or private address
(or a hostname that answers public to the admin-schema write-time check and
private to the fetch-time resolver) was connected to and received the configured
API key. The admin schema gate is a write boundary only -- it neither pins the
address nor covers config loaded straight from disk -- so the fetch site itself
has to fail closed.

Every network effect below is a fake resolver / fake transport: no real sockets
and no real DNS are used. The first test pins the pre-fix behaviour of the raw
transport; the rest pin the post-fix behaviour of the guarded fetch site.
"""

import pytest
from urllib.request import Request

from src.core.automod.ai import custom as custom_mod
from src.core.automod.ai.custom import CustomAdapter
from src.utils.security import URLValidator

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _Resp:
    """Minimal context-managed response used by both fake transports."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"flagged": false}'


def _fake_pinned_transport(monkeypatch):
    """Replace the guarded fetch site's pinned transport with a recorder."""
    calls = []

    def fake(url, *, timeout=30, method="GET", data=None, headers=None):
        calls.append((url, method, timeout, dict(headers or {})))
        return _Resp()

    monkeypatch.setattr(custom_mod, "_pinned_urlopen", fake)
    return calls


def test_raw_transport_still_fetches_loopback_without_an_ip_check(monkeypatch):
    """Pre-fix proof: ``outbound.urlopen`` has no IP-range screening.

    This is the transport ``CustomAdapter`` called before the fix. With a fake
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
    with outbound.urlopen(Request("http://127.0.0.1:8080/latest/meta-data"), timeout=7):
        pass

    assert seen and seen[0][0] == "http://127.0.0.1:8080/latest/meta-data"
    assert seen[0][1] == 7


def test_loopback_literal_endpoint_is_denied_before_the_fetch(monkeypatch):
    calls = _fake_pinned_transport(monkeypatch)
    adapter = CustomAdapter(
        {"endpoint_url": "http://127.0.0.1:8080/moderate", "api_key": "s3cr3t"}
    )

    with pytest.raises(custom_mod.AIBackendError):
        adapter.check_content("hello")

    assert calls == [], "loopback endpoint reached the transport"


def test_hostname_resolving_to_private_ip_is_denied(monkeypatch):
    calls = _fake_pinned_transport(monkeypatch)
    # Fake DNS answer: public-looking name, link-local address (the metadata
    # service). The real URLValidator still does the IP screening on the answer.
    import socket

    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))
        ],
    )
    adapter = CustomAdapter(
        {"endpoint_url": "https://rebind.example/moderate", "api_key": "s3cr3t"}
    )

    with pytest.raises(custom_mod.AIBackendError):
        adapter.check_content("hello")

    assert calls == [], "private-resolving endpoint reached the transport"


def test_resolution_failure_fails_closed(monkeypatch):
    import socket

    calls = _fake_pinned_transport(monkeypatch)

    def boom(host, port):
        raise socket.gaierror("DNS resolution failed for rebind.example")

    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", boom)
    adapter = CustomAdapter(
        {"endpoint_url": "https://rebind.example/moderate", "api_key": "s3cr3t"}
    )

    with pytest.raises(custom_mod.AIBackendError):
        adapter.check_content("hello")

    assert calls == [], "unresolvable endpoint reached the transport"


def test_public_endpoint_is_still_fetched_and_pinned(monkeypatch):
    """Control: a legitimate public URL is fetched with the API key intact."""
    calls = _fake_pinned_transport(monkeypatch)
    monkeypatch.setattr(
        URLValidator,
        "validate_url_for_request",
        lambda self, url: ("moderation.example", "93.184.216.34"),
    )
    adapter = CustomAdapter(
        {"endpoint_url": "https://moderation.example/moderate", "api_key": "s3cr3t"}
    )

    result = adapter.check_content("hello")

    assert result.flagged is False
    assert len(calls) == 1
    url, method, timeout, headers = calls[0]
    assert url == "https://moderation.example/moderate"
    assert method == "POST"
    assert timeout == 10.0
    assert headers.get("Authorization") == "Bearer s3cr3t"
