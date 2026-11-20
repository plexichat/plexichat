"""Regression guard for batched Valkey string reads (MGET).

A hydration path that fetched N keys used to issue N single GETs, i.e. N
network round trips. The Valkey client now exposes a fail-closed bulk primitive
(``mget``/``amget``) and the JSON bulk path (``mget_json``/``amget_json``)
routes through it, so the same fetch costs one round trip.

These tests pin four properties:

* round-trip reduction: N keys -> exactly one MGET (never N GETs);
* positional/miss semantics are identical to N single GETs (with prefixing);
* an empty selection performs no round trip at all;
* fail-closed: a driver failure raises ``ValkeyOperationError`` instead of
  returning an all-miss list a caller could mistake for "every key absent".
"""

import json

import pytest

from src.core.database.valkey_client import ValkeyClient, ValkeyOperationError


class CountingMgetClient:
    """Minimal GLIDE-like sync handle that counts GET vs MGET round trips."""

    def __init__(self, data=None):
        # Keys are stored already prefixed, as the real server sees them.
        self.data = dict(data or {})
        self.mget_calls = 0
        self.get_calls = 0
        self.fail = False

    def mget(self, keys):
        self.mget_calls += 1
        if self.fail:
            raise RuntimeError("connection reset")
        return [self.data.get(key) for key in keys]

    def get(self, key):
        self.get_calls += 1
        return self.data.get(key)


class CountingMgetAsyncClient(CountingMgetClient):
    async def mget(self, keys):
        return super().mget(keys)

    async def get(self, key):
        return super().get(key)


def _client(handle):
    """Build a ValkeyClient around a fake handle without needing config setup."""
    client = object.__new__(ValkeyClient)
    client.enabled = True
    client._connected = True
    client._aclient = None
    client._aconnected = False
    client._client = handle
    client.key_prefix = "pc:"
    return client


def test_mget_collapses_n_gets_into_one_round_trip():
    handle = CountingMgetClient({"pc:a": b"1", "pc:c": b"3"})
    client = _client(handle)

    values = client.mget(["a", "b", "c"])

    assert values == ["1", None, "3"]  # aligned, missing key -> None
    assert handle.mget_calls == 1
    assert handle.get_calls == 0  # no per-key GET fan-out


def test_mget_prefixed_keys_are_not_double_prefixed():
    handle = CountingMgetClient({"pc:a": b"value"})
    client = _client(handle)

    assert client.mget(["a", "pc:a"]) == ["value", "value"]
    assert handle.mget_calls == 1


def test_mget_empty_selection_is_no_round_trip():
    handle = CountingMgetClient()
    client = _client(handle)

    assert client.mget([]) == []
    assert handle.mget_calls == 0


def test_mget_fail_closed_on_driver_error():
    handle = CountingMgetClient()
    handle.fail = True
    client = _client(handle)

    # A driver error must surface as an exception, not as [None, None, ...]
    # which a caller could read as "no cached data" and fail open.
    with pytest.raises(ValkeyOperationError):
        client.mget(["a", "b"])


def test_mget_json_single_round_trip_decodes_payloads():
    handle = CountingMgetClient(
        {
            "pc:u:1": json.dumps({"n": 1}).encode(),
            "pc:u:2": json.dumps({"n": 2}).encode(),
        }
    )
    client = _client(handle)

    assert client.mget_json(["u:1", "u:2", "u:3"]) == {
        "u:1": {"n": 1},
        "u:2": {"n": 2},
    }
    assert handle.mget_calls == 1
    assert handle.get_calls == 0


def test_mget_json_fail_closed_on_driver_error():
    handle = CountingMgetClient()
    handle.fail = True
    client = _client(handle)

    with pytest.raises(ValkeyOperationError):
        client.mget_json(["u:1", "u:2"])


@pytest.mark.asyncio
async def test_amget_and_amget_json_single_round_trip_and_fail_closed():
    handle = CountingMgetAsyncClient(
        {
            "pc:a": b"1",
            "pc:j": json.dumps({"ok": True}).encode(),
        }
    )
    aclient = object.__new__(ValkeyClient)
    aclient.enabled = True
    aclient._client = None
    aclient._connected = False
    aclient._aclient = handle
    aclient._aconnected = True
    aclient.key_prefix = "pc:"

    assert await aclient.amget(["a", "missing"]) == ["1", None]
    assert await aclient.amget_json(["j", "missing"]) == {"j": {"ok": True}}
    assert handle.mget_calls == 2  # one per bulk call, zero single GETs
    assert handle.get_calls == 0

    handle.fail = True
    with pytest.raises(ValkeyOperationError):
        await aclient.amget(["a"])
    with pytest.raises(ValkeyOperationError):
        await aclient.amget_json(["j"])


# ---------------------------------------------------------------------------
# cache layer: bulk read uses one MGET and offers a fail-closed mode
# ---------------------------------------------------------------------------


def test_cache_get_many_batches_n_keys_into_one_mget(monkeypatch):
    from src.core.database.cache import operations

    class FakeCache:
        def __init__(self):
            self.mget_calls = 0
            self.get_calls = 0
            self.store = {f"k:{i}": {"n": i} for i in range(50)}

        def mget_json(self, keys):
            self.mget_calls += 1
            return {k: self.store[k] for k in keys if k in self.store}

        def get_json(self, key):
            self.get_calls += 1
            return self.store.get(key)

    fake = FakeCache()
    monkeypatch.setattr(operations, "get_client", lambda: fake)
    monkeypatch.setattr(operations, "is_available", lambda: True)

    keys = [f"k:{i}" for i in range(50)]
    out = operations.cache_get_many(keys)

    assert len(out) == 50
    assert fake.mget_calls == 1  # 50 GETs -> 1 MGET
    assert fake.get_calls == 0
