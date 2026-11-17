"""Regressions for residual Web Push SSRF vectors.

Two checks are covered that the literal-IP gate alone does not provide:

* ``inet_aton(3)`` spellings of a private address (decimal, hex, octal,
  truncated) are accepted by the system resolver but not by ``ipaddress``;
  a registered subscription using one of them would POST into the internal
  network unless we normalise them before deciding.
* pywebpush follows redirects, so an otherwise-public endpoint can answer 30x
  with a Location pointing at loopback/metadata. The transport must pass a
  session that refuses redirects.
"""

import sys
import types

import pytest

from src.core.push.manager import PushManager
from src.core.push.manager.web_push import (
    _NoRedirectSession,
    _require_public_endpoint,
)

pytestmark = pytest.mark.unit


class TestObfuscatedEndpointIPs:
    @pytest.mark.parametrize(
        "endpoint",
        [
            # 127.0.0.1 in every inet_aton(3) spelling.
            "https://2130706433/secret",
            "https://0x7f000001/secret",
            "https://0177.0.0.1/secret",
            "https://127.1/secret",
            "https://2130706433./secret",  # trailing dot must not fool it
            # 169.254.169.254 (cloud metadata) as a decimal integer.
            "https://2852039166/latest/meta-data/",
            # 192.168.1.1 as a decimal integer.
            "https://3232235777/admin",
            # 0.0.0.0 / unspecified.
            "https://0/secret",
            # IPv4-mapped IPv6 with an obfuscated v4 tail.
            "https://[::ffff:2130706433]/secret",
        ],
    )
    def test_obfuscated_internal_endpoint_is_refused(self, endpoint):
        with pytest.raises(ValueError):
            _require_public_endpoint(endpoint)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://push.example/ep",
            "https://8.8.8.8/ep",
            "https://fcm.googleapis.com/fcm/send/abc",
        ],
    )
    def test_public_endpoint_remains_allowed(self, endpoint):
        assert _require_public_endpoint(endpoint) == endpoint

    def test_sender_skips_transport_for_obfuscated_endpoint(self, monkeypatch):
        calls = []
        fake = types.ModuleType("pywebpush")

        class WebPushException(Exception):
            pass

        def webpush(**kwargs):
            calls.append(kwargs)

        fake.WebPushException = WebPushException
        fake.webpush = webpush
        monkeypatch.setitem(sys.modules, "pywebpush", fake)

        mgr = object.__new__(PushManager)
        mgr._vapid_available = True
        rec = {
            "token": "https://2130706433/secret",
            "p256dh": "key",
            "auth": "secret",
        }
        config = {"vapid_private_key": "k", "vapid_subject": "mailto:a@b.c"}

        assert mgr._send_web_push(rec, "t", "b", None, config) is None
        assert calls == []


class TestNoRedirectTransport:
    def test_session_forces_allow_redirects_false(self, monkeypatch):
        calls = []

        class FakeSession:
            def post(self, url, **kwargs):
                calls.append((url, kwargs))
                return "response"

            def close(self):
                calls.append(("closed", {}))

        fake_requests = types.ModuleType("requests")
        fake_requests.Session = FakeSession
        monkeypatch.setitem(sys.modules, "requests", fake_requests)

        with _NoRedirectSession() as session:
            session.post("https://push.example/ep", data="x", timeout=5)

        assert calls[0] == (
            "https://push.example/ep",
            {"data": "x", "timeout": 5, "allow_redirects": False},
        )
        assert calls[-1] == ("closed", {})

    def test_sender_passes_no_redirect_session_to_transport(self, monkeypatch):
        seen = {}
        fake = types.ModuleType("pywebpush")

        class WebPushException(Exception):
            pass

        def webpush(**kwargs):
            seen.update(kwargs)

        fake.WebPushException = WebPushException
        fake.webpush = webpush
        monkeypatch.setitem(sys.modules, "pywebpush", fake)

        mgr = object.__new__(PushManager)
        mgr._vapid_available = True
        rec = {"token": "https://push.example/ep", "p256dh": "key", "auth": "secret"}
        config = {"vapid_private_key": "k", "vapid_subject": "mailto:a@b.c"}

        assert mgr._send_web_push(rec, "t", "b", None, config) is True
        assert isinstance(seen.get("requests_session"), _NoRedirectSession)
