"""Shared push-payload serialization is computed once per announcement fan-out.

A bulk announcement validates the same ``data`` mapping once per recipient
(``send_bulk_push`` -> ``send_push`` -> ``_bounded_data``). ``_bounded_data``
encoded every value with ``json.dumps`` just to measure its byte size, so N
recipients re-encoded the same six id fields and mention type N times. The
bounded size cache keyed by each value's canonical input makes those N encodes
one, without weakening the size bounds or changing the delivered payload.
"""

import json

import pytest

import src.core.push.manager.delivery as delivery
from src.core.push.manager.delivery import (
    MAX_PUSH_DATA_VALUE_BYTES,
    DeliveryMixin,
)

pytestmark = pytest.mark.unit

BIG = 9007199254740993


class FakeDB:
    def execute(self, sql, params=()):
        return None


class FakeManager(DeliveryMixin):
    def __init__(self, user_ids):
        self._db = FakeDB()
        self._notifications = None
        self._users = set(user_ids)
        self.sent = []

    def get_user_tokens(self, user_id, platform=None):
        if user_id not in self._users:
            return []
        return [{"token": f"u{user_id}-d0", "platform": "android"}]

    def _send_to_platform(
        self, token_record, title, body, data, badge, sound, priority
    ):
        self.sent.append(dict(data))
        return True

    def _get_timestamp(self):
        return 1234

    @staticmethod
    def _token_blind_index(token):
        return f"idx:{token}"


def _announcement_data(i=0):
    """Six shared fields plus one per-notification id (one push per recipient)."""
    return {
        "notification_id": str(BIG + i),
        "message_id": str(BIG + 3),
        "conversation_id": str(BIG + 4),
        "server_id": str(BIG + 5),
        "channel_id": str(BIG + 6),
        "thread_id": str(BIG + 7),
        "mention_type": "everyone",
    }


@pytest.fixture(autouse=True)
def reset_cache():
    with delivery._DATA_VALUE_SIZE_CACHE_LOCK:
        delivery._DATA_VALUE_SIZE_CACHE.clear()
        delivery._DATA_VALUE_SIZE_STATS["hits"] = 0
        delivery._DATA_VALUE_SIZE_STATS["misses"] = 0
    yield


def _count_dumps(monkeypatch):
    real = delivery.json.dumps
    calls = []

    def counting(*args, **kwargs):
        calls.append(args[0] if args else None)
        return real(*args, **kwargs)

    monkeypatch.setattr(delivery.json, "dumps", counting)
    return calls


class TestAnnouncementFanout:
    def test_shared_values_serialized_once_for_all_recipients(self, monkeypatch):
        mgr = FakeManager(range(60))
        data = _announcement_data()
        calls = _count_dumps(monkeypatch)

        total = mgr.send_bulk_push(list(range(60)), "announcement", "body", data)

        assert total == 60
        # One encode per distinct value, not one per recipient. The old path
        # encoded all 7 values for the batch AND again for every send_push.
        assert len(calls) == len(data)
        stats = delivery.get_push_data_serialization_stats()
        assert stats["misses"] == len(data)
        assert stats["hits"] == len(data) * 60
        assert stats["size"] == len(data)

    def test_repeated_direct_sends_reuse_serialization(self, monkeypatch):
        mgr = FakeManager([1])
        mgr.get_user_tokens = lambda user_id, platform=None: [
            {"token": "u1-d0", "platform": "android"}
        ]
        data = _announcement_data()
        calls = _count_dumps(monkeypatch)

        for _ in range(50):
            assert mgr.send_push(1, "t", "b", data) == 1

        assert len(calls) == len(data)

    def test_delivered_payload_is_unchanged(self):
        mgr = FakeManager([7])
        data = _announcement_data()
        # Warm the cache through one recipient, then deliver again.
        mgr.send_push(7, "t", "b", data)
        mgr.send_push(7, "t", "b", data)

        assert mgr.sent == [data, data]
        assert all(isinstance(v, str) or v is None for v in mgr.sent[0].values())


class TestBoundsNotWeakened:
    def test_changed_value_gets_fresh_size_and_is_still_bounded(self):
        small = _announcement_data()
        assert delivery._bounded_data(small) == small

        oversized = dict(small)
        oversized["message_id"] = "x" * (MAX_PUSH_DATA_VALUE_BYTES + 1)
        with pytest.raises(ValueError, match="maximum size"):
            delivery._bounded_data(oversized)

    def test_non_serializable_value_still_rejected(self):
        class NotSerializable:
            pass

        with pytest.raises(ValueError, match="not JSON serialisable"):
            delivery._bounded_data({"bad": NotSerializable()})

    def test_oversized_key_never_cached(self):
        huge = "y" * (delivery._MAX_DATA_VALUE_CACHE_KEY_CHARS + 1)
        with pytest.raises(ValueError, match="maximum size"):
            delivery._bounded_data({"blob": huge})
        assert delivery.get_push_data_serialization_stats()["size"] == 0

    def test_cache_is_bounded(self):
        for i in range(delivery._MAX_DATA_VALUE_SIZE_CACHE_ENTRIES * 2):
            delivery._bounded_data({"v": f"value-{i}"})
        stats = delivery.get_push_data_serialization_stats()
        assert stats["size"] == stats["max_entries"]
        assert stats["misses"] > stats["max_entries"]

    def test_canonical_json_bytes_match_direct_serialization(self):
        for value in ("123", 9007199254740993, None, True, 1.5, 'a"b\\c\n'):
            expected = len(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            assert delivery._serialized_value_bytes(value) == expected
