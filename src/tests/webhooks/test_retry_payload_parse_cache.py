"""Regression tests for the bounded webhook payload-parse cache.

One event fanned out to N webhook endpoints produces N retry rows carrying the
same canonical payload JSON string. Claiming those rows used to run
``json.loads`` once per row. The queue must decode the shared string once,
reuse that decode for the rest of the fan-out, and still hand every delivery
an independent, correct payload dict.
"""

import json

import pytest

from src.core.webhooks import retry as retry_module
from src.core.webhooks.retry import WebhookRetryQueue, _parse_payload_json


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Keep the module-level cache isolated between tests."""
    previous = retry_module._PARSED_PAYLOAD_CACHE
    retry_module._PARSED_PAYLOAD_CACHE = retry_module._ParsedPayloadCache(max_entries=4)
    try:
        yield
    finally:
        retry_module._PARSED_PAYLOAD_CACHE = previous


def _enqueue_fanout(queue, payload, count):
    for webhook_id in range(1, count + 1):
        queue.enqueue(
            webhook_id=webhook_id,
            event_type="message.created",
            payload=payload,
            endpoint_url="https://example.com/hook",
        )


class TestIdenticalOutput:
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"a": 1},
            {"b": 2, "a": 1},
            {"nested": {"list": [1, 2, {"x": "y"}]}, "none": None},
            {"unicode": "naïve — 日本語", "emoji": "🚀"},
        ],
    )
    def test_cached_parse_matches_json_loads(self, payload):
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected = json.loads(raw)
        assert _parse_payload_json(raw) == expected
        assert _parse_payload_json(raw) == expected

    def test_distinct_payloads_never_share_a_result(self):
        first = _parse_payload_json(json.dumps({"event": "a"}))
        second = _parse_payload_json(json.dumps({"event": "b"}))
        assert first == {"event": "a"}
        assert second == {"event": "b"}


class TestFewerOperations:
    def test_fanout_parses_shared_payload_once(self, db):
        queue = WebhookRetryQueue(db)
        payload = {
            "content": "hello " * 200,
            "embeds": [{"title": "t" * 100} for _ in range(10)],
            "author": {"id": "123456789"},
        }
        _enqueue_fanout(queue, payload, 25)

        # enqueue stringifies snowflake ids, so derive the stored key from a
        # row itself rather than guessing the normalized shape.
        stored_raw = db.fetch_one("SELECT payload FROM webhook_retry_queue LIMIT 1")[
            "payload"
        ]

        real_loads = json.loads
        calls = []

        def counting_loads(s, *args, **kwargs):
            if s == stored_raw:
                calls.append(s)
            return real_loads(s, *args, **kwargs)

        retry_module.json.loads = counting_loads
        try:
            due = queue.get_pending_retries()
        finally:
            retry_module.json.loads = real_loads

        assert len(due) == 25
        assert len(calls) == 1
        stats = retry_module._PARSED_PAYLOAD_CACHE.stats()
        assert stats["hits"] == 24
        assert stats["misses"] == 1

    def test_every_delivery_gets_an_independent_dict(self, db):
        queue = WebhookRetryQueue(db)
        _enqueue_fanout(queue, {"event": "message.created", "id": "42"}, 5)

        due = queue.get_pending_retries()
        assert len(due) == 5
        assert all(
            row["payload"] == {"event": "message.created", "id": "42"} for row in due
        )
        assert len({row["payload_raw_json"] for row in due}) == 1

        # Mutating one claimed row must not poison the other rows or the cache.
        due[0]["payload"]["event"] = "mutated"
        again = queue.__class__(db).get_pending_retries()
        # Rows are now 'delivering', so nothing new is claimed; parse directly
        # from the shared raw string to prove the cached value stayed intact.
        assert _parse_payload_json(due[0]["payload_raw_json"]) == {
            "event": "message.created",
            "id": "42",
        }
        assert again == []


class TestNoStaleData:
    def test_changed_raw_json_yields_a_fresh_parse(self):
        first = _parse_payload_json(json.dumps({"a": "x"}))
        second = _parse_payload_json(json.dumps({"a": "x" * 100}))
        assert first == {"a": "x"}
        assert second == {"a": "x" * 100}

    def test_cache_is_bounded(self):
        for i in range(20):
            assert _parse_payload_json(json.dumps({"n": i})) == {"n": i}
        stats = retry_module._PARSED_PAYLOAD_CACHE.stats()
        assert stats["size"] == stats["max_entries"] == 4
        assert stats["hits"] == 0
        assert stats["misses"] == 20


class TestChecksNotWeakened:
    def test_malformed_payload_becomes_empty_dict(self, db):
        queue = WebhookRetryQueue(db)
        record = queue.enqueue(
            webhook_id=1,
            event_type="message.created",
            payload={"m": "a"},
            endpoint_url="https://example.com/hook",
        )
        db.execute(
            "UPDATE webhook_retry_queue SET payload = ? WHERE id = ?",
            ("not-json{", record["id"]),
        )
        due = queue.get_pending_retries()
        assert len(due) == 1
        assert due[0]["payload"] == {}

    def test_oversized_key_bypasses_cache(self):
        oversized = json.dumps({"blob": "x" * (1024 * 1024 + 1)})
        assert _parse_payload_json(oversized)["blob"] == "x" * (1024 * 1024 + 1)
        assert retry_module._PARSED_PAYLOAD_CACHE.stats()["size"] == 0
