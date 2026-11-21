"""Regressions for deep hardening of the push manager.

Covers three weaknesses that remained after the earlier push audit:

* the synchronous send paths fanned out without the device/user caps that
  the async paths already enforced (unbounded fan-out / worker stall);
* a Web Push subscription endpoint could target loopback/private hosts,
  turning pywebpush into an SSRF client (``require_https_url`` only checks
  the scheme, not the destination);
* APNs custom data could overwrite the reserved ``aps`` payload or bloat it
  with unbounded keys (payload injection).
"""

import sys
import types

import pytest

import src.core.push.manager.delivery as delivery
from src.core.push.manager import PushManager
from src.core.push.manager.apns import (
    MAX_APNS_DATA_KEY_LENGTH,
    MAX_APNS_DATA_KEYS,
    _reset_apns_http_client,
)
from src.core.push.manager.delivery import DeliveryMixin
from src.core.push.manager.web_push import _require_public_endpoint

pytestmark = pytest.mark.unit


class _FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))


class _SyncManager(DeliveryMixin):
    """Minimal DeliveryMixin harness for the synchronous send paths."""

    def __init__(self, tokens_by_user, send_fn):
        self._db = _FakeDB()
        self._notifications = None
        self._tokens = tokens_by_user
        self._send_fn = send_fn

    def get_user_tokens(self, user_id, platform=None):
        return list(self._tokens.get(user_id, []))

    def _send_to_platform(
        self, token_record, title, body, data, badge, sound, priority
    ):
        return self._send_fn(token_record)

    def _get_timestamp(self):
        return 1234

    @staticmethod
    def _token_blind_index(token):  # pyright: ignore[reportIncompatibleMethodOverride]
        return f"idx:{token}"


def _token(name):
    return {"token": name, "platform": "android"}


class TestBoundedSyncFanout:
    def test_send_push_caps_devices(self):
        sent = []
        tokens = [_token(f"d{i}") for i in range(60)]
        mgr = _SyncManager({1: tokens}, lambda rec: sent.append(rec["token"]) or True)

        count = mgr.send_push(1, "title", "body")

        assert count == mgr.MAX_TOKENS_PER_USER
        assert sent == [f"d{i}" for i in range(mgr.MAX_TOKENS_PER_USER)]

    def test_send_bulk_push_caps_users(self):
        seen = []
        mgr = _SyncManager({}, lambda rec: True)
        mgr.send_push = lambda user_id, title, body, data=None: (
            seen.append(user_id) or 1
        )

        users = list(range(delivery.MAX_BULK_PUSH_USERS + 50))
        total = mgr.send_bulk_push(users, "title", "body")

        assert seen == users[: delivery.MAX_BULK_PUSH_USERS]
        assert total == delivery.MAX_BULK_PUSH_USERS


class TestWebPushEndpointSSRF:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://127.0.0.1/secret",
            "https://[::1]/secret",
            "https://10.0.0.5/latest/meta-data",
            "https://192.168.1.1/admin",
            "https://169.254.169.254/latest/meta-data/",
            "https://localhost/secret",
            "https://metadata.google.internal/computeMetadata",
            "https://host.internal/secret",
        ],
    )
    def test_internal_endpoint_is_refused(self, endpoint):
        with pytest.raises(ValueError):
            _require_public_endpoint(endpoint)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://push.example/ep",
            "https://fcm.googleapis.com/fcm/send/abc",
            "https://8.8.8.8/ep",
        ],
    )
    def test_public_endpoint_is_allowed(self, endpoint):
        assert _require_public_endpoint(endpoint) == endpoint

    def test_sender_does_not_call_transport_for_internal_endpoint(self, monkeypatch):
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
            "token": "https://169.254.169.254/latest/meta-data/",
            "p256dh": "key",
            "auth": "secret",
        }
        config = {"vapid_private_key": "k", "vapid_subject": "mailto:a@b.c"}

        assert mgr._send_web_push(rec, "t", "b", None, config) is None
        assert calls == []


class _FakeResponse:
    status_code = 200

    def json(self):
        return {}


class _FakeClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.posts = []
        _FakeClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()


def _install_apns_fakes(monkeypatch):
    fake_jwt = types.ModuleType("jwt")
    fake_jwt.encode = lambda payload, key, algorithm=None, headers=None: "signed"
    fake_httpx = types.ModuleType("httpx")
    fake_httpx.Client = _FakeClient
    monkeypatch.setitem(sys.modules, "jwt", fake_jwt)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    _reset_apns_http_client()
    _FakeClient.instances.clear()


def _apns_config():
    return {
        "apns_team_id": "TEAM",
        "apns_key_id": "KEY",
        "apns_bundle_id": "com.example.app",
        "apns_p8_key": "fake-p8",
    }


class TestApnsPayloadInjection:
    def test_reserved_aps_key_cannot_overwrite_alert(self, monkeypatch):
        _install_apns_fakes(monkeypatch)
        mgr = object.__new__(PushManager)
        mgr._apns_available = True
        PushManager._apns_jwt_cache.clear()

        data = {"aps": "evil", "ok": "yes"}
        assert (
            mgr._send_apns("tok", "t", "b", data, None, "default", _apns_config())
            is True
        )

        sent = _FakeClient.instances[-1].posts[-1]["json"]
        assert isinstance(sent["aps"], dict)
        assert sent["aps"]["alert"] == {"title": "t", "body": "b"}
        assert sent["ok"] == "yes"

    def test_oversized_key_and_excess_keys_are_dropped(self, monkeypatch):
        _install_apns_fakes(monkeypatch)
        mgr = object.__new__(PushManager)
        mgr._apns_available = True
        PushManager._apns_jwt_cache.clear()

        data = {"x" * (MAX_APNS_DATA_KEY_LENGTH + 1): "big"}
        data.update({f"k{i}": str(i) for i in range(MAX_APNS_DATA_KEYS + 5)})
        assert (
            mgr._send_apns("tok", "t", "b", data, None, "default", _apns_config())
            is True
        )

        sent = _FakeClient.instances[-1].posts[-1]["json"]
        assert len(sent) <= MAX_APNS_DATA_KEYS
        assert "x" * (MAX_APNS_DATA_KEY_LENGTH + 1) not in sent
