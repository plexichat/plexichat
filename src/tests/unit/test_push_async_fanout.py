"""Bounded async push fan-out with backpressure."""

import asyncio

import pytest

import src.core.push.manager.delivery as delivery
from src.core.push.manager.async_fanout import bounded_fanout
from src.core.push.manager.delivery import DeliveryMixin


class FakeDB:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))


class FakeManager(DeliveryMixin):
    def __init__(self, tokens_by_user, send_fn):
        self._db = FakeDB()
        self._notifications = None
        self._tokens = tokens_by_user
        self._send_fn = send_fn
        self.active = 0
        self.max_active = 0

    def get_user_tokens(self, user_id, platform=None):
        return list(self._tokens.get(user_id, []))

    def _send_to_platform(
        self, token_record, title, body, data, badge, sound, priority
    ):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            return self._send_fn(token_record)
        finally:
            self.active -= 1

    def _get_timestamp(self):
        return 1234

    @staticmethod
    def _token_blind_index(token):
        return f"idx:{token}"


def _token(name):
    return {"token": name, "platform": "android"}


@pytest.mark.asyncio
async def test_bounded_fanout_limits_concurrency_and_keeps_order():
    active = 0
    max_active = 0

    async def worker(item):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.005)
        active -= 1
        return item * 2

    results = await bounded_fanout(range(20), worker, concurrency=3, max_queue=5)

    assert results == [i * 2 for i in range(20)]
    assert max_active <= 3


@pytest.mark.asyncio
async def test_bounded_fanout_applies_backpressure_at_queue_bound():
    started = asyncio.Event()
    release = asyncio.Event()
    yielded = 0

    async def worker(item):
        started.set()
        await release.wait()
        return item

    class CountingIterable:
        def __iter__(self):
            nonlocal yielded
            for i in range(100):
                yielded += 1
                yield i

    # One worker + a queue of one: producer can stage at most two items before
    # queue.put() blocks, so item generation pauses (backpressure).
    task = asyncio.create_task(
        bounded_fanout(CountingIterable(), worker, concurrency=1, max_queue=1)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0.02)
    assert yielded <= 3, f"producer ran ahead of the bounded queue: {yielded}"

    release.set()
    results = await asyncio.wait_for(task, timeout=2)
    assert results == list(range(100))


@pytest.mark.asyncio
async def test_bounded_fanout_isolates_timeouts_and_failures():
    async def worker(item):
        if item == 1:
            raise RuntimeError("boom")
        if item == 2:
            await asyncio.sleep(0.2)
        return item

    results = await bounded_fanout(
        [0, 1, 2, 3], worker, concurrency=4, max_queue=4, timeout=0.01
    )
    assert results[0] == 0
    assert results[1] is None
    assert results[2] is None  # timed out
    assert results[3] == 3


@pytest.mark.asyncio
async def test_send_push_async_bounds_devices_and_cleans_invalid_tokens(monkeypatch):
    monkeypatch.setattr(delivery, "DEVICE_FANOUT_CONCURRENCY", 2)

    def send(token_record):
        name = token_record["token"]
        if name == "bad":
            return False
        if name == "skip":
            return None
        return True

    manager = FakeManager(
        {7: [_token("a"), _token("bad"), _token("skip"), _token("b")]}, send
    )

    sent = await manager.send_push_async(7, "title", "body")

    assert sent == 2
    assert manager.max_active <= 2
    updates = [c for c in manager._db.calls if c[0].startswith("UPDATE")]
    deletes = [c for c in manager._db.calls if c[0].startswith("DELETE")]
    assert len(updates) == 2
    assert [c[1][0] for c in deletes] == ["idx:bad"]


@pytest.mark.asyncio
async def test_send_bulk_push_async_bounds_concurrent_users(monkeypatch):
    monkeypatch.setattr(delivery, "DEVICE_FANOUT_CONCURRENCY", 1)
    monkeypatch.setattr(delivery, "USER_FANOUT_CONCURRENCY", 2)

    manager = FakeManager(
        {uid: [_token(f"t{uid}-1"), _token(f"t{uid}-2")] for uid in range(6)},
        lambda record: True,
    )

    total = await manager.send_bulk_push_async(list(range(6)), "title", "body")

    assert total == 12
    assert manager.max_active <= 2


@pytest.mark.asyncio
async def test_send_push_async_respects_device_cap(monkeypatch):
    monkeypatch.setattr(delivery, "DEVICE_FANOUT_CONCURRENCY", 4)
    manager = FakeManager(
        {1: [_token(f"t{i}") for i in range(40)]}, lambda record: True
    )
    sent = await manager.send_push_async(1, "title", "body")
    assert sent == manager.MAX_TOKENS_PER_USER
