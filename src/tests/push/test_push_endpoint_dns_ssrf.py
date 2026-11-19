"""Web Push endpoint SSRF via DNS answers.

``_require_public_endpoint`` used to reject only literal private IPs and a
handful of internal hostnames. An authenticated caller could therefore
register an endpoint such as ``https://attacker.example/push`` whose DNS
records point at loopback or the cloud metadata address; pywebpush resolves
the name itself and would POST the encrypted payload into the internal
network. This test pins the DNS check at the validation boundary.
"""

import socket
import sys
import types

import pytest

from src.core.push.manager import PushManager
from src.core.push.manager.web_push import _require_public_endpoint

pytestmark = pytest.mark.unit


def _answer(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 0))]


def _stub_dns(monkeypatch, mapping):
    """Route DNS answers through ``mapping``; unknown names raise NXDOMAIN."""

    def fake_getaddrinfo(host, port=None, *args, **kwargs):
        if host in mapping:
            return _answer(mapping[host])
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(
        "src.core.push.manager.web_push.socket.getaddrinfo", fake_getaddrinfo
    )


class TestDnsBasedEndpointSsrf:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.0.0.5",
            "192.168.1.1",
            "169.254.169.254",
            "100.64.0.1",  # CGNAT: not is_private on every Python release
            "::1",
            "fd00::1",
        ],
    )
    def test_endpoint_resolving_to_private_ip_is_refused(self, monkeypatch, address):
        _stub_dns(monkeypatch, {"attacker.example": address})
        with pytest.raises(ValueError, match="resolves to a non-public IP"):
            _require_public_endpoint("https://attacker.example/push")

    def test_every_dns_answer_is_checked_not_just_the_first(self, monkeypatch):
        """A mixed A-record set must not slip a private target through."""

        def fake_getaddrinfo(host, port=None, *args, **kwargs):
            assert host == "mixed.example"
            return _answer("93.184.216.34") + _answer("169.254.169.254")

        monkeypatch.setattr(
            "src.core.push.manager.web_push.socket.getaddrinfo", fake_getaddrinfo
        )
        with pytest.raises(ValueError, match="resolves to a non-public IP"):
            _require_public_endpoint("https://mixed.example/push")

    def test_public_dns_answer_is_allowed(self, monkeypatch):
        _stub_dns(monkeypatch, {"push.example": "93.184.216.34"})
        endpoint = "https://push.example/push"
        assert _require_public_endpoint(endpoint) == endpoint

    def test_unresolvable_name_is_not_crashed_on(self, monkeypatch):
        # NXDOMAIN must not blow up the sender; the transport could not reach
        # the host anyway. The endpoint still passes the other structural
        # checks unchanged.
        _stub_dns(monkeypatch, {})
        endpoint = "https://unresolvable.example/push"
        assert _require_public_endpoint(endpoint) == endpoint

    def test_sender_skips_transport_for_private_dns_answer(self, monkeypatch):
        calls = []
        fake = types.ModuleType("pywebpush")

        class WebPushException(Exception):
            pass

        def webpush(**kwargs):
            calls.append(kwargs)

        fake.WebPushException = WebPushException
        fake.webpush = webpush
        monkeypatch.setitem(sys.modules, "pywebpush", fake)
        _stub_dns(monkeypatch, {"attacker.example": "127.0.0.1"})

        mgr = object.__new__(PushManager)
        mgr._vapid_available = True
        rec = {
            "token": "https://attacker.example/push",
            "p256dh": "key",
            "auth": "secret",
        }
        config = {"vapid_private_key": "k", "vapid_subject": "mailto:a@b.c"}

        assert mgr._send_web_push(rec, "t", "b", None, config) is None
        assert calls == []
