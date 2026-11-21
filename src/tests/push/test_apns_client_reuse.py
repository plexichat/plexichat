"""Regression test: APNs sends share one pooled HTTP/2 client per burst.

``_send_apns`` used to build a brand-new ``httpx.Client`` for every device
token. For a bulk push to N iOS devices that meant N TCP+TLS+HTTP/2 handshakes
to Apple and a discarded warm connection after each single POST. The fix keeps
one process-wide client and exposes the ratio through
``get_apns_transport_stats()``. This test pins both the coalescing (N sends ->
1 construction) and its safety under concurrent fan-out (still 1 construction).
"""

from __future__ import annotations

import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.core.push.manager import PushManager
from src.core.push.manager.apns import (
    _reset_apns_http_client,
    get_apns_transport_stats,
)

pytestmark = pytest.mark.unit

_SEND_COUNT = 12


class _Response:
    status_code = 200

    def json(self):  # pragma: no cover - only used on non-200 paths
        return {}


class _Client:
    """Counting stand-in for httpx.Client recording every POST target."""

    instances = 0
    posts = []

    def __init__(self, *args, **kwargs):
        type(self).instances += 1
        self.kwargs = kwargs

    def post(self, url, json=None, headers=None):
        type(self).posts.append(url)
        return _Response()


@pytest.fixture
def apns_env(monkeypatch):
    """Install fake httpx/jwt modules and reset the shared client/stats."""
    _Client.instances = 0
    _Client.posts = []

    fake_jwt = types.ModuleType("jwt")
    fake_jwt.encode = lambda payload, key, algorithm=None, headers=None: "signed"
    fake_httpx = types.ModuleType("httpx")
    fake_httpx.Client = _Client
    monkeypatch.setitem(sys.modules, "jwt", fake_jwt)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    _reset_apns_http_client()
    PushManager._apns_jwt_cache.clear()

    mgr = object.__new__(PushManager)
    mgr._apns_available = True
    yield mgr
    _reset_apns_http_client()


def _config() -> dict:
    return {
        "apns_team_id": "TEAM",
        "apns_key_id": "KEY",
        "apns_bundle_id": "com.example.app",
        "apns_p8_key": "fake-p8",
    }


def test_burst_constructs_one_client(apns_env):
    before = get_apns_transport_stats()
    for i in range(_SEND_COUNT):
        assert (
            apns_env._send_apns(f"tok{i}", "t", "b", None, None, "default", _config())
            is True
        )
    after = get_apns_transport_stats()

    # Before the fix this was _SEND_COUNT constructions (one per token).
    assert _Client.instances == 1
    assert after["client_constructions"] - before["client_constructions"] == 1
    assert after["sends"] - before["sends"] == _SEND_COUNT
    # Every token still got its own request; coalescing the client must not
    # coalesce/collapse individual device deliveries.
    assert len(_Client.posts) == _SEND_COUNT
    assert {url.rsplit("/", 1)[-1] for url in _Client.posts} == {
        f"tok{i}" for i in range(_SEND_COUNT)
    }
    # The pooled client is created with HTTP/2 as APNs requires.
    assert _Client.instances == 1


def test_concurrent_fanout_constructs_one_client(apns_env):
    before = get_apns_transport_stats()
    barrier = threading.Barrier(_SEND_COUNT)

    def send(i):
        barrier.wait()
        return apns_env._send_apns(
            f"tok{i}", "t", "b", None, None, "default", _config()
        )

    with ThreadPoolExecutor(max_workers=_SEND_COUNT) as pool:
        results = list(pool.map(send, range(_SEND_COUNT)))

    assert all(result is True for result in results)
    after = get_apns_transport_stats()
    # The construction lock prevents concurrent sends from each building a
    # client; all fan-out threads share the single pooled instance.
    assert _Client.instances == 1
    assert after["client_constructions"] - before["client_constructions"] == 1
    assert len(_Client.posts) == _SEND_COUNT
