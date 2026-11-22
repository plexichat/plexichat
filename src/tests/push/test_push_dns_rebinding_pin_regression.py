"""Regression: Web Push must connect to the IP its SSRF gate validated.

``_require_public_endpoint`` used to resolve a subscription endpoint once to
check that it is public and then hand the *hostname* URL to pywebpush. requests
resolves that name a second time, so a rebinding DNS server can answer with a
public address for the check and with loopback / the cloud-metadata address for
the connection -- a classic SSRF that the one-shot IP check does not stop.

The gate now returns the address it approved and the transport pins the
connection to it, while keeping the real hostname for ``Host``, TLS SNI,
certificate verification and the VAPID ``aud`` claim. The literal-IP path needs
no pin (there is no second lookup) and stays blocked when the literal is
non-public.
"""

import socket
import sys
import types

import pytest
from requests.models import Request

from src.core.push.manager import PushManager
from src.core.push.manager.web_push import (
    _NoRedirectSession,
    _require_public_endpoint,
    _resolve_public_endpoint,
)

pytestmark = pytest.mark.unit

PUBLIC_IP = "93.184.216.34"
PRIVATE_IP = "169.254.169.254"


def _resolver(*answers):
    """A getaddrinfo stub that returns successive answers (rebinding sim)."""
    calls = []
    remaining = list(answers)

    def fake(host, port, *args, **kwargs):
        calls.append(host)
        ip = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]

    return fake, calls


class TestResolverReturnsPinnedAddress:
    def test_dns_name_yields_validated_address(self, monkeypatch):
        # Unique URL per test: the shared validation cache must not turn a
        # later rebinding assertion into a cache hit from an earlier test.
        fake, calls = _resolver(PUBLIC_IP)
        monkeypatch.setattr(socket, "getaddrinfo", fake)

        endpoint, pinned = _resolve_public_endpoint("https://push-pin-1.example/ep")

        assert endpoint == "https://push-pin-1.example/ep"
        assert pinned == PUBLIC_IP
        assert calls == ["push-pin-1.example"]  # one lookup by the gate

    def test_wrapper_keeps_returning_the_endpoint_string(self, monkeypatch):
        fake, _ = _resolver(PUBLIC_IP)
        monkeypatch.setattr(socket, "getaddrinfo", fake)
        assert (
            _require_public_endpoint("https://push-pin-2.example/ep")
            == "https://push-pin-2.example/ep"
        )

    def test_literal_needs_no_pin_but_private_literal_is_refused(self):
        # No second lookup exists for a literal, so no pin is necessary.
        endpoint, pinned = _resolve_public_endpoint("https://8.8.8.8/ep")
        assert endpoint == "https://8.8.8.8/ep"
        assert pinned is None

        with pytest.raises(ValueError):
            _resolve_public_endpoint(f"https://{PRIVATE_IP}/ep")

    def test_rebound_second_answer_is_still_rejected(self, monkeypatch):
        # All answers from a single lookup are checked, not just the first.
        def mixed(host, port, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PRIVATE_IP, 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", mixed)
        with pytest.raises(ValueError):
            _resolve_public_endpoint("https://push-pin-3.example/ep")


class TestPinnedTransport:
    def test_session_connects_to_pinned_ip_with_original_tls_identity(self):
        session = _NoRedirectSession(pinned_ip=PUBLIC_IP, tls_hostname="push.example")
        # Environment proxies would resolve the hostname again on our behalf.
        assert session._session.trust_env is False

        adapter = session._session.get_adapter("https://push.example/ep")
        assert adapter._target == PUBLIC_IP

        request = Request("POST", "https://push.example/ep").prepare()
        pool = adapter.get_connection_with_tls_context(request, True)
        # The TCP connection is made to the validated IP ...
        assert pool.host == PUBLIC_IP
        # ... while TLS SNI, cert verification and requests' Host header stay
        # bound to the hostname the endpoint actually claims.
        assert pool.conn_kw["server_hostname"] == "push.example"
        assert pool.assert_hostname == "push.example"
        # The URL handed to requests is unchanged, so requests/urllib3 derives
        # Host from the real hostname (not the pinned address) as usual.
        assert request.url == "https://push.example/ep"
        session.close()

    def test_rebinding_after_validation_does_not_change_the_connection(
        self, monkeypatch
    ):
        """A second DNS answer (private) must never redirect the pinned POST."""
        fake, calls = _resolver(PUBLIC_IP, PRIVATE_IP)
        monkeypatch.setattr(socket, "getaddrinfo", fake)

        _, pinned = _resolve_public_endpoint("https://push-pin-4.example/ep")
        session = _NoRedirectSession(
            pinned_ip=pinned, tls_hostname="push-pin-4.example"
        )
        adapter = session._session.get_adapter("https://push-pin-4.example/ep")

        # The transport never re-resolves: it still targets the approved IP.
        # (A second lookup here would return PRIVATE_IP -- the rebind.)
        assert fake("push-pin-4.example", None)[0][4][0] == PRIVATE_IP
        assert adapter._target == pinned == PUBLIC_IP
        session.close()

    def test_sender_pins_the_resolved_address_at_delivery(self, monkeypatch):
        fake, calls = _resolver(PUBLIC_IP, PRIVATE_IP)
        monkeypatch.setattr(socket, "getaddrinfo", fake)

        seen = {}
        pywebpush = types.ModuleType("pywebpush")

        class WebPushException(Exception):
            pass

        def webpush(**kwargs):
            seen.update(kwargs)

        pywebpush.WebPushException = WebPushException
        pywebpush.webpush = webpush
        monkeypatch.setitem(sys.modules, "pywebpush", pywebpush)

        mgr = object.__new__(PushManager)
        mgr._vapid_available = True
        record = {
            "token": "https://push-pin-5.example/ep",
            "p256dh": "key",
            "auth": "secret",
        }
        config = {"vapid_private_key": "k", "vapid_subject": "mailto:a@b.c"}

        assert mgr._send_web_push(record, "t", "b", None, config) is True
        session = seen["requests_session"]
        assert isinstance(session, _NoRedirectSession)
        adapter = session._session.get_adapter("https://push-pin-5.example/ep")
        assert adapter._target == PUBLIC_IP
        # Validation resolved the host exactly once; the rebind was never used.
        assert calls == ["push-pin-5.example"]
