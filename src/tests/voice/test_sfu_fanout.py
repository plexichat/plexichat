"""The read-once fan-out relay.

A ``RemoteStreamTrack`` is backed by a single queue, so two senders calling
``recv()`` on the same track each *take* a frame -- the stream is split, not
shared. The SFU forwards one produced track to every other peer, so without a
relay each peer receives a fraction of the media and nothing about the sender
objects reveals it. These tests pin the property that matters: every
subscriber receives every frame, independently of the others' pace.
"""

import asyncio

import pytest

from src.core.voice.signaling.sfu.fanout import DEFAULT_MAX_LAG, TrackFanout


class _ScriptedSource:
    """Yields ``total`` integer frames, then ``None`` (end of stream)."""

    kind = "audio"
    label = "scripted"

    def __init__(self, total: int):
        self.total = total
        self.read = 0

    async def recv(self):
        # Yield between frames so a consumer can drain as production proceeds,
        # the way real-time media arrives rather than as one burst.
        await asyncio.sleep(0)
        if self.read >= self.total:
            return None
        self.read += 1
        return self.read

    def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_every_subscriber_receives_every_frame() -> None:
    source = _ScriptedSource(5)
    relay = TrackFanout(source)

    a = relay.subscribe()
    b = relay.subscribe()

    got_a = [await a.recv() for _ in range(5)]
    got_b = [await b.recv() for _ in range(5)]

    assert got_a == [1, 2, 3, 4, 5], "first view missed frames"
    assert got_b == [1, 2, 3, 4, 5], "second view got a *different* stream"
    assert source.read == 5, "the source was read more than once per frame"

    await relay.aclose()


@pytest.mark.asyncio
async def test_end_of_stream_reaches_every_view() -> None:
    relay = TrackFanout(_ScriptedSource(1))
    a = relay.subscribe()
    b = relay.subscribe()

    assert await a.recv() == 1
    assert await b.recv() == 1
    assert await asyncio.wait_for(a.recv(), 2) is None
    assert await asyncio.wait_for(b.recv(), 2) is None

    await relay.aclose()


@pytest.mark.asyncio
async def test_a_stopped_view_does_not_affect_the_others() -> None:
    relay = TrackFanout(_ScriptedSource(4))
    a = relay.subscribe()
    b = relay.subscribe()

    a.stop()

    assert relay.subscriber_count == 1
    assert await asyncio.wait_for(b.recv(), 2) == 1
    # A stopped view reports end of stream rather than reading the relay.
    assert a.readyState == "ended"
    assert await a.recv() is None
    assert getattr(a.fanout.source, "stopped", False) is False

    await relay.aclose()


@pytest.mark.asyncio
async def test_a_lagging_view_sheds_frames_without_blocking_the_rest() -> None:
    """One slow sender must not stall the read loop for everyone else."""
    relay = TrackFanout(_ScriptedSource(40), max_lag=2)
    slow = relay.subscribe()
    fast = relay.subscribe()

    # Drain the fast view continuously; never touch the slow one.
    received = []
    for _ in range(40):
        frame = await asyncio.wait_for(fast.recv(), 2)
        if frame is None:
            break
        received.append(frame)

    assert received == list(range(1, 41)), "the fast view lost frames"
    assert relay.dropped_frames > 0, "the slow view did not shed under back-pressure"
    # Shedding, not growing: the slow view's queue stays within its budget.
    assert slow._queue.qsize() <= 2

    await relay.aclose()


@pytest.mark.asyncio
async def test_subscribing_after_a_view_stops_restarts_the_relay() -> None:
    relay = TrackFanout(_ScriptedSource(6))
    first = relay.subscribe()
    assert await first.recv() == 1
    first.stop()
    # Let the read loop observe that it has no subscribers.
    await asyncio.sleep(0)

    second = relay.subscribe()
    assert second._queue.maxsize == DEFAULT_MAX_LAG
    # Whatever remains is delivered to the new view.
    assert await asyncio.wait_for(second.recv(), 2) is not None

    await relay.aclose()
