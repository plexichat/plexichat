"""Regression tests for the bounded webhook payload-size serialization cache.

One event fanned out to N webhook endpoints produces N retry rows carrying the
same canonical payload. Delivery must derive the byte size once and reuse it,
without changing the bound, the delivered payload, or ever returning a size
computed from different input.
"""

import json

import pytest

from src.core.webhooks import delivery as delivery_module
from src.core.webhooks.delivery import (
    MAX_DELIVERY_PAYLOAD_BYTES,
    WebhookDeliverer,
    WebhookDeliveryError,
    _PayloadSizeCache,
    _payload_serialized_size,
)

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Keep the module-level cache isolated between tests."""
    previous = delivery_module._PAYLOAD_SIZE_CACHE
    delivery_module._PAYLOAD_SIZE_CACHE = _PayloadSizeCache(max_entries=4)
    try:
        yield
    finally:
        delivery_module._PAYLOAD_SIZE_CACHE = previous


class _FakeResponse:
    status_code = 200


class _FakeClient:
    def __init__(self):
        self.payloads = []

    def post(self, url, json=None, headers=None, extensions=None):
        self.payloads.append(json)
        return _FakeResponse()


def _expected_size(payload):
    return len(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _record(payload, raw=None):
    record = {"endpoint_url": "https://example.com/hook", "payload": payload}
    if raw is not None:
        record["payload_raw_json"] = raw
    return record


@pytest.fixture
def deliverer(monkeypatch):
    client = _FakeClient()
    d = WebhookDeliverer(client=client)
    # Isolate the payload-bound path from SSRF/URL validation.
    d._build_target = staticmethod(
        lambda url, hostname, ip, headers: (url, dict(headers), {})
    )
    monkeypatch.setattr(
        delivery_module,
        "validate_delivery_endpoint",
        lambda url, allow_private_network=False: ("example.com", PUBLIC_IP),
    )
    return d, client


class TestIdenticalOutput:
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"a": 1},
            {"b": 2, "a": 1},  # insertion order differs from canonical order
            {"nested": {"list": [1, 2, {"x": "y"}]}, "none": None},
            {"unicode": "naïve — 日本語", "emoji": "🚀"},
        ],
    )
    def test_cached_size_matches_direct_serialization(self, payload):
        raw = json.dumps(payload, sort_keys=True)
        expected = _expected_size(payload)
        # First call is a miss, second is a hit: both must be identical.
        assert _payload_serialized_size(payload, raw) == expected
        assert _payload_serialized_size(payload, raw) == expected

    def test_missing_or_malformed_raw_json_still_computes(self):
        payload = {"a": 1, "b": [1, 2, 3]}
        assert _payload_serialized_size(payload) == _expected_size(payload)
        assert _payload_serialized_size(payload, 12345) == _expected_size(payload)
        assert _payload_serialized_size(payload, "") == _expected_size(payload)

    def test_delivered_payload_is_unchanged_by_caching(self, deliverer):
        d, client = deliverer
        payload = {"b": 2, "a": {"id": "1"}}
        raw = json.dumps(payload, sort_keys=True)
        assert d.deliver(_record(payload, raw)) == 200
        assert d.deliver(_record(payload, raw)) == 200
        assert client.payloads == [payload, payload]


class TestFewerOperations:
    def test_fanout_serializes_shared_payload_once(self, deliverer, monkeypatch):
        d, _client = deliverer
        payload = {
            "content": "hello " * 200,
            "embeds": [{"title": "t" * 100} for _ in range(10)],
        }
        raw = json.dumps(payload, sort_keys=True)

        real_dumps = delivery_module.json.dumps
        calls = []

        def counting_dumps(*args, **kwargs):
            calls.append(args[0] if args else None)
            return real_dumps(*args, **kwargs)

        monkeypatch.setattr(delivery_module.json, "dumps", counting_dumps)
        for _ in range(50):
            assert d.deliver(_record(payload, raw)) == 200

        # Without the cache this is 50 re-encodes of the same object.
        assert len(calls) == 1
        stats = delivery_module._PAYLOAD_SIZE_CACHE.stats()
        assert stats["hits"] == 49
        assert stats["misses"] == 1

    def test_retry_queue_preserves_canonical_json_for_the_cache(self, db):
        from src.core.webhooks.retry import WebhookRetryQueue

        queue = WebhookRetryQueue(db)
        payload = {"event": "message.created", "id": "42"}
        for webhook_id in (1, 2, 3):
            queue.enqueue(
                webhook_id=webhook_id,
                event_type="message.created",
                payload=payload,
                endpoint_url="https://example.com/hook",
            )
        due = queue.get_pending_retries()
        assert len(due) == 3
        raw_values = {r["payload_raw_json"] for r in due}
        # Enqueue persists the same compact canonical form that signing and
        # delivery use, so all fan-out rows share one cache key.
        assert raw_values == {
            json.dumps(payload, sort_keys=True, separators=(",", ":"))
        }
        for row in due:
            assert row["payload"] == payload


class TestNoStaleData:
    def test_changed_payload_yields_a_fresh_size(self):
        first = {"a": "x"}
        second = {"a": "x" * 100}
        first_raw, second_raw = json.dumps(first), json.dumps(second)
        assert _payload_serialized_size(first, first_raw) == _expected_size(first)
        assert _payload_serialized_size(second, second_raw) == _expected_size(second)
        assert _expected_size(second) != _expected_size(first)

    def test_reordering_keys_is_one_cache_entry(self):
        # sort_keys makes the canonical raw string stable regardless of the
        # payload dict's insertion order, so equal payloads share one entry.
        raw = json.dumps({"a": 1, "b": 2}, sort_keys=True)
        _payload_serialized_size({"a": 1, "b": 2}, raw)
        _payload_serialized_size({"b": 2, "a": 1}, raw)
        assert delivery_module._PAYLOAD_SIZE_CACHE.stats()["size"] == 1

    def test_cache_is_bounded(self):
        for i in range(20):
            payload = {"n": i}
            raw = json.dumps(payload, sort_keys=True)
            assert _payload_serialized_size(payload, raw) == _expected_size(payload)
        stats = delivery_module._PAYLOAD_SIZE_CACHE.stats()
        assert stats["size"] == stats["max_entries"] == 4
        assert stats["hits"] == 0
        assert stats["misses"] == 20


class TestChecksNotWeakened:
    def test_oversized_payload_still_rejected_via_cache(self, deliverer):
        d, _client = deliverer
        payload = {"blob": "x" * (MAX_DELIVERY_PAYLOAD_BYTES + 1)}
        raw = json.dumps(payload, sort_keys=True)
        with pytest.raises(WebhookDeliveryError, match="maximum delivery size"):
            d.deliver(_record(payload, raw))

    def test_oversized_payload_still_rejected_without_cache(self, deliverer):
        d, _client = deliverer
        payload = {"blob": "x" * (MAX_DELIVERY_PAYLOAD_BYTES + 1)}
        with pytest.raises(WebhookDeliveryError, match="maximum delivery size"):
            d.deliver(_record(payload))

    def test_non_serializable_payload_still_rejected(self, deliverer):
        d, _client = deliverer

        class NotSerializable:
            pass

        with pytest.raises(WebhookDeliveryError, match="not JSON serializable"):
            d.deliver(_record({"bad": NotSerializable()}))
