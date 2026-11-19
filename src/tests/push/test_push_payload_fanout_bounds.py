"""Bound the per-device push payload before a fan-out is created.

``title``/``body`` are bounded at the fan-out boundary because they are copied
into every device's outbound request. ``data`` and ``sound`` are copied there
too but were left unbounded: a caller passing a multi-megabyte ``data`` dict
(or a giant ``sound`` string) made each of the (up to) 10 devices / 1000 users
serialise that blob again. These regressions pin the new pre-fan-out gate.
"""

import pytest

import src.core.push.manager.delivery as delivery
from src.core.push.manager.delivery import (
    MAX_PUSH_DATA_KEYS,
    MAX_PUSH_DATA_VALUE_BYTES,
    MAX_SOUND_LENGTH,
    DeliveryMixin,
    _bounded_data,
)

pytestmark = pytest.mark.unit


class _FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))


class _SyncManager(DeliveryMixin):
    """Minimal DeliveryMixin harness that records per-device sends."""

    def __init__(self, tokens_by_user):
        self._db = _FakeDB()
        self._notifications = None
        self._tokens = tokens_by_user
        self.sent = []

    def get_user_tokens(self, user_id, platform=None):
        return list(self._tokens.get(user_id, []))

    def _send_to_platform(
        self, token_record, title, body, data, badge, sound, priority
    ):
        self.sent.append(
            {
                "token": token_record["token"],
                "data": data,
                "sound": sound,
            }
        )
        return True

    def _get_timestamp(self):
        return 1234

    @staticmethod
    def _token_blind_index(token):  # pyright: ignore[reportIncompatibleMethodOverride]
        return f"idx:{token}"


def _tokens(count):
    return [{"token": f"d{i}", "platform": "android"} for i in range(count)]


class TestBoundedDataHelper:
    def test_none_becomes_empty_dict(self):
        assert _bounded_data(None) == {}

    def test_valid_data_is_preserved(self):
        data = {"a": 1, "b": ["x", "y"], 7: "numeric-key"}
        assert _bounded_data(data) == {"a": 1, "b": ["x", "y"], "7": "numeric-key"}

    def test_non_mapping_rejected(self):
        with pytest.raises(ValueError, match="mapping"):
            _bounded_data(["not", "a", "dict"])

    def test_too_many_keys_rejected(self):
        with pytest.raises(ValueError, match="keys"):
            _bounded_data({f"k{i}": i for i in range(MAX_PUSH_DATA_KEYS + 1)})

    def test_oversized_value_rejected(self):
        with pytest.raises(ValueError, match="value exceeds"):
            _bounded_data({"blob": "x" * (MAX_PUSH_DATA_VALUE_BYTES + 1)})

    def test_oversized_total_rejected(self):
        # Each value is within the per-value cap, but 17 of them exceed the
        # total cap while staying under the key-count cap.
        chunk = "x" * (MAX_PUSH_DATA_VALUE_BYTES - 96)
        data = {f"k{i}": chunk for i in range(17)}
        with pytest.raises(ValueError, match="total size"):
            _bounded_data(data)

    def test_non_serialisable_value_rejected(self):
        class Bad:
            pass

        with pytest.raises(ValueError, match="serialisable"):
            _bounded_data({"bad": Bad()})


class TestPreFanoutGate:
    def test_send_push_rejects_oversized_data_before_any_device_send(self):
        mgr = _SyncManager({1: _tokens(3)})
        blob = {"blob": "x" * (MAX_PUSH_DATA_VALUE_BYTES + 1)}

        with pytest.raises(ValueError, match="value exceeds"):
            mgr.send_push(1, "title", "body", data=blob)

        assert mgr.sent == [], "no device should be contacted for an invalid payload"
        assert mgr._db.calls == [], "no token bookkeeping should run either"

    def test_send_push_rejects_oversized_sound(self):
        mgr = _SyncManager({1: _tokens(3)})

        with pytest.raises(ValueError, match="sound"):
            mgr.send_push(1, "title", "body", sound="z" * (MAX_SOUND_LENGTH + 1))

        assert mgr.sent == []

    def test_send_bulk_push_rejects_before_iterating_users(self):
        mgr = _SyncManager({})
        called = []
        mgr.send_push = lambda *a, **k: called.append(a) or 1

        with pytest.raises(ValueError, match="keys"):
            mgr.send_bulk_push(
                list(range(10)),
                "title",
                "body",
                data={f"k{i}": i for i in range(MAX_PUSH_DATA_KEYS + 1)},
            )

        assert called == [], "the bulk fan-out must fail before any per-user work"

    def test_valid_data_and_sound_reach_every_device(self):
        mgr = _SyncManager({1: _tokens(3)})
        data = {"notification_id": "123", "mention_type": "USER"}

        sent = mgr.send_push(1, "title", "body", data=data, sound="ping")

        assert sent == 3
        assert [call["data"] for call in mgr.sent] == [data] * 3
        assert [call["sound"] for call in mgr.sent] == ["ping"] * 3
