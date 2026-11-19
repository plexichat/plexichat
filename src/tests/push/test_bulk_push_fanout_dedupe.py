"""Bulk push fan-out must dedupe recipients and bound work at the cap.

``send_bulk_push[_async]`` used to do ``list(user_ids)[:MAX_BULK_PUSH_USERS]``
(or ``[int(uid) for uid in user_ids][:MAX_BULK_PUSH_USERS]``): the *entire*
input was materialised/coerced before the cap was applied. Two consequences:

* a repeated id produced one full device fan-out per occurrence (duplicate
  delivery), and
* a huge (or endlessly repeated) input was fully drained before the cap was
  reached (unbounded work on the request thread / event loop).

These regressions pin the fix: dedupe in first-seen order, stop streaming at
the cap, and never route one user's notification to another user's devices.
"""

import pytest

import src.core.push.manager.delivery as delivery
from src.core.push.manager.delivery import (
    MAX_BULK_PUSH_USERS,
    DeliveryMixin,
    _bounded_unique_users,
)

pytestmark = pytest.mark.unit


class FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))


class FakeManager(DeliveryMixin):
    """Record which user was looked up and which device token was sent to."""

    def __init__(self, tokens_by_user):
        self._db = FakeDB()
        self._notifications = None
        self._tokens = tokens_by_user
        self.lookups = []
        self.sent = []

    def get_user_tokens(self, user_id, platform=None):
        self.lookups.append(user_id)
        return list(self._tokens.get(user_id, []))

    def _send_to_platform(
        self, token_record, title, body, data, badge, sound, priority
    ):
        self.sent.append((token_record.get("owner"), token_record["token"]))
        return True

    def _get_timestamp(self):
        return 1234

    @staticmethod
    def _token_blind_index(token):  # pyright: ignore[reportIncompatibleMethodOverride]
        return f"idx:{token}"


def _tokens(user_id, count=1):
    return [
        {"token": f"u{user_id}-d{i}", "platform": "android", "owner": user_id}
        for i in range(count)
    ]


def _manager(user_ids, devices=1):
    return FakeManager({uid: _tokens(uid, devices) for uid in user_ids})


# --- dedupe ------------------------------------------------------------------


def test_sync_bulk_dedupes_repeated_users():
    mgr = _manager([1, 2, 3])
    mgr.send_bulk_push([1, 1, 1, 2, 2, 3], "t", "b")

    assert mgr.lookups == [1, 2, 3]
    assert [owner for owner, _ in mgr.sent] == [1, 2, 3]


@pytest.mark.asyncio
async def test_async_bulk_dedupes_repeated_users():
    mgr = _manager([1, 2, 3])
    total = await mgr.send_bulk_push_async([1, 1, 1, 2, 2, 3], "t", "b")

    assert total == 3
    assert sorted(mgr.lookups) == [1, 2, 3]
    assert sorted(owner for owner, _ in mgr.sent) == [1, 2, 3]


def test_helper_preserves_first_seen_order():
    assert _bounded_unique_users([9, 2, 9, 7, 2], 100) == [9, 2, 7]


# --- hard bound / bounded work ------------------------------------------------


def test_sync_bulk_hard_caps_unique_users_and_stops_generator_early():
    consumed = []

    def huge():
        for i in range(100_000):
            consumed.append(i)
            yield i

    mgr = _manager(range(MAX_BULK_PUSH_USERS))
    mgr.send_bulk_push(huge(), "t", "b")

    assert len(mgr.lookups) == MAX_BULK_PUSH_USERS
    # Stops within one id of the ceiling instead of draining 100 000 ids.
    assert len(consumed) == MAX_BULK_PUSH_USERS + 1


@pytest.mark.asyncio
async def test_async_bulk_hard_caps_unique_users_and_stops_generator_early():
    consumed = []

    def huge():
        for i in range(100_000):
            consumed.append(i)
            yield i

    mgr = _manager(range(MAX_BULK_PUSH_USERS))
    await mgr.send_bulk_push_async(huge(), "t", "b")

    assert len(mgr.lookups) == MAX_BULK_PUSH_USERS
    assert len(consumed) == MAX_BULK_PUSH_USERS + 1


# --- no cross-audience leak ---------------------------------------------------


def test_dedupe_does_not_cross_user_devices():
    """Each recipient gets only their own device(s), exactly once.

    A dedupe bug that keyed on the shared payload instead of the user id, or
    that reused one user's token list for another, would leak a notification
    across audiences. Distinct tokens per user make that visible.
    """
    mgr = _manager([10, 20, 30], devices=2)
    mgr.send_bulk_push([10, 20, 10, 30, 20], "t", "b")

    by_owner = {}
    for owner, token in mgr.sent:
        by_owner.setdefault(owner, []).append(token)

    assert set(by_owner) == {10, 20, 30}
    for owner, tokens in by_owner.items():
        assert tokens == [f"u{owner}-d0", f"u{owner}-d1"]


@pytest.mark.asyncio
async def test_bounding_to_cap_never_delivers_to_a_non_recipient(monkeypatch):
    """Truncation keeps only the first N *unique* recipients, no others."""
    cap = 4
    monkeypatch.setattr(delivery, "MAX_BULK_PUSH_USERS", cap)

    audience = list(range(20))
    mgr = _manager(audience)
    await mgr.send_bulk_push_async(audience, "t", "b")

    assert sorted(set(mgr.lookups)) == list(range(cap))
    assert all(owner < cap for owner, _ in mgr.sent)


# --- existing checks are not weakened ----------------------------------------


def test_oversized_payload_still_rejected_before_any_user_work():
    from src.core.push.manager.delivery import MAX_PUSH_DATA_KEYS

    mgr = _manager([1])
    with pytest.raises(ValueError, match="keys"):
        mgr.send_bulk_push(
            [1, 2, 3],
            "t",
            "b",
            data={f"k{i}": i for i in range(MAX_PUSH_DATA_KEYS + 1)},
        )
    assert mgr.lookups == []
