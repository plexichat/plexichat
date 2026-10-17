"""Unit tests for the APNs sender internals.

Covers the three hardening fixes in ``PushManager._send_apns``:

* the P8 ``\\\\n`` normalisation must be a plain replacement (the old
  ``unicode_escape`` round-trip mangled backslashes/non-ASCII bytes);
* the per-(team, key) JWT cache must be reused while valid and refreshed
  after expiry, atomically under the class lock;
* custom payload values must be coerced losslessly (mappings/sequences as
  JSON) and uncoercible values dropped with a debug log, never silently.
"""

from __future__ import annotations

import json
import sys
import threading
import types

import pytest

from src.core.push.manager import PushManager

pytestmark = pytest.mark.unit


def _config(p8_key: str = "fake-p8") -> dict:
    return {
        "apns_team_id": "TEAM123",
        "apns_key_id": "KEY123",
        "apns_bundle_id": "com.example.app",
        "apns_p8_key": p8_key,
    }


class _FakeResponse:
    status_code = 200

    def json(self):  # pragma: no cover - only used on non-200 paths
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


def _install_fakes(monkeypatch, jwt_encode=None):
    """Install fake ``jwt``/``httpx`` modules; return captured encode calls."""
    calls = []

    fake_jwt = types.ModuleType("jwt")

    def encode(payload, key, algorithm=None, headers=None):
        calls.append(
            {"payload": payload, "key": key, "algorithm": algorithm, "headers": headers}
        )
        if jwt_encode is not None:
            return jwt_encode(payload, key, algorithm, headers)
        return f"signed-{len(calls)}"

    fake_jwt.encode = encode

    fake_httpx = types.ModuleType("httpx")
    fake_httpx.Client = _FakeClient

    monkeypatch.setitem(sys.modules, "jwt", fake_jwt)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    return calls


@pytest.fixture
def apns_manager(db):
    mgr = PushManager(db, notifications_module=None)
    mgr._apns_available = True
    PushManager._apns_jwt_cache.clear()
    _FakeClient.instances.clear()
    return mgr


def test_p8_backslash_n_normalised_by_replacement(db, monkeypatch, apns_manager):
    calls = _install_fakes(monkeypatch)
    escaped = "-----BEGIN PRIVATE KEY-----\\nABC\\nDEF\\n-----END PRIVATE KEY-----"
    result = apns_manager._send_apns(
        "device-token", "t", "b", None, None, "default", _config(escaped)
    )
    assert result is True
    key = calls[0]["key"]
    assert "\n" in key
    assert "\\n" not in key
    # No unicode_escape mangling: every escape became exactly one newline.
    assert key.count("\n") == escaped.count("\\n")


def test_p8_literal_newlines_untouched(db, monkeypatch, apns_manager):
    calls = _install_fakes(monkeypatch)
    literal = "-----BEGIN PRIVATE KEY-----\nABC\nDEF\n-----END PRIVATE KEY-----"
    apns_manager._send_apns(
        "device-token", "t", "b", None, None, "default", _config(literal)
    )
    assert calls[0]["key"] == literal


def test_jwt_cache_reused_while_valid(db, monkeypatch, apns_manager):
    calls = _install_fakes(monkeypatch)
    cfg = _config()
    assert apns_manager._send_apns("tok", "t", "b", None, None, "default", cfg) is True
    assert apns_manager._send_apns("tok", "t", "b", None, None, "default", cfg) is True
    # Second send reused the cached token instead of re-signing.
    assert len(calls) == 1
    headers = [_FakeClient.instances[i].posts[0]["headers"] for i in range(2)]
    assert headers[0]["authorization"] == headers[1]["authorization"]


def test_jwt_cache_refreshed_after_expiry(db, monkeypatch, apns_manager):
    calls = _install_fakes(monkeypatch)
    cfg = _config()
    apns_manager._send_apns("tok", "t", "b", None, None, "default", cfg)
    # Force expiry of the cached entry.
    for cache_key in list(PushManager._apns_jwt_cache):
        token, _exp = PushManager._apns_jwt_cache[cache_key]
        PushManager._apns_jwt_cache[cache_key] = (token, 0)
    apns_manager._send_apns("tok", "t", "b", None, None, "default", cfg)
    assert len(calls) == 2


def test_jwt_cache_lock_is_class_level():
    assert isinstance(PushManager._apns_jwt_lock, type(threading.Lock()))


def test_payload_coercion_json_and_scalars(db, monkeypatch, apns_manager):
    _install_fakes(monkeypatch)
    data = {
        "plain": "keep",
        "count": 7,
        "meta": {"a": 1, "b": [1, 2]},
        "tags": ["x", "y"],
        9: "numeric-key",
    }
    apns_manager._send_apns("tok", "t", "b", data, None, "default", _config())
    sent = _FakeClient.instances[0].posts[0]["json"]
    assert sent["aps"]["alert"] == {"title": "t", "body": "b"}
    assert sent["plain"] == "keep"
    assert sent["count"] == "7"
    assert sent["meta"] == json.dumps({"a": 1, "b": [1, 2]})
    assert sent["tags"] == json.dumps(["x", "y"])
    assert sent["9"] == "numeric-key"


def test_payload_uncoercible_value_dropped_without_raising(
    db, monkeypatch, apns_manager
):
    _install_fakes(monkeypatch)

    class BadStr:
        def __str__(self):
            raise RuntimeError("cannot stringify")

    data = {"good": "yes", "bad": BadStr()}
    result = apns_manager._send_apns("tok", "t", "b", data, None, "default", _config())
    assert result is True
    sent = _FakeClient.instances[0].posts[0]["json"]
    assert sent["good"] == "yes"
    assert "bad" not in sent
