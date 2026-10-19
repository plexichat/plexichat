"""Read-once fan-out for a track forwarded to several peers.

An aiortc ``RemoteStreamTrack`` is backed by a single queue, so two senders
calling ``recv()`` on the *same* track split the frames between them rather
than both receiving them. An SFU forwards one produced track to every other
peer, so a shared track delivers a fraction of the media to each peer -- and
comparing the sender objects (which is all the layer-hint suite did) looks
perfectly correct.

:class:`TrackFanout` owns the single read loop and copies every frame into a
bounded per-view queue, so each :class:`FanoutView` sees the whole stream.
Each sender gets its own view, so one sender stopping detaches that view only
-- the relay and the shared track underneath it keep running.

The queue is not the only thing each sender needs of its own: aiortc encodes
each sender's frames on the loop's executor, so with two subscribers the same
media would be encoded by two threads at once. :func:`_private_frame` gives
every view its own buffer when more than one is subscribed; see its docstring
for the crash that sharing one cost.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Optional

import utils.logger as logger

# aiortc is optional, and the relay is only used by the aiortc backend. The
# subclass matters: ``RTCPeerConnection.addTrack`` only binds a track that
# ``isinstance`` of ``MediaStreamTrack`` -- anything else is interpreted as a
# *kind string* and the sender silently ends up with ``track is None``, so the
# media is never sent and nothing raises.
try:  # pragma: no cover - exercised wherever aiortc is installed
    from aiortc.mediastreams import MediaStreamTrack as _AiortcTrack

    _TRACK_BASE: Any = _AiortcTrack
except ImportError:  # pragma: no cover - aiortc absent
    # Fallback when the optional aiortc backend is absent. The relay is
    # only wired to that backend, so this never carries media; Any
    # preserves the subclass relationship so module import succeeds.
    _TRACK_BASE = object

_MediaStreamTrack = _TRACK_BASE


# Frames buffered per view before the oldest is dropped. A sender that stalls
# must not grow its buffer without bound -- a real SFU sheds rather than
# queueing without limit -- and a small budget keeps latency tight.
DEFAULT_MAX_LAG = 8


def _av_module() -> Any:
    """The ``av`` module, or ``None`` where it is not installed.

    ``av`` is only reachable through aiortc, which is optional, so the relay
    must import it lazily rather than fail to load in a deployment that has no
    media backend at all.
    """
    try:
        import av
    except ImportError:  # pragma: no cover - aiortc/av absent
        return None
    return av


def _private_frame(frame: Any) -> Any:
    """A copy of ``frame`` that only one view's encoder will read.

    aiortc encodes a sender's frames off the event loop (``loop.run_in_executor(
    None, encoder.encode, frame)``), and a fan-out hands the same frame object to
    every view. With two subscribers that is two encoders running
    ``Vp8Encoder.encode`` on one ``av.VideoFrame`` at once, and its
    ``frame.reformat`` -- a libswscale read of the frame's planes -- is not safe
    to run concurrently: measured on this box, two threads encoding one shared
    frame crashed the interpreter (SIGSEGV in ``asyncio_0``) in 6 of 6 trials,
    while two threads encoding separate frames never did. In CI it surfaced as
    pipeline #2091's red ``test:shuffled_order``: the test's teardown closed the
    loop, the loop waited on an encoding thread wedged inside ``reformat``, and
    pytest-timeout fired at 60s. The same wedged thread is the shape of the
    "xdist node down" stall #2089 was retried for.

    Copying is what makes each encoder's input its own: the relay reads the
    source once, then gives every view a buffer that nothing else encodes. Video
    is copied as ``yuv420p`` because that is the pixel format both aiortc video
    encoders convert to anyway, so the copy replaces the per-encoder conversion
    rather than adding one. A frame type this cannot copy (a ``Packet`` already
    encoded, or a format ``to_ndarray`` rejects) is passed through unchanged: it
    is the encoder's input, not a buffer two encoders share.

    :param frame: A frame read from the relay's source.
    :returns: A frame safe to hand to one encoder, or ``frame`` itself.
    """
    av = _av_module()
    if av is None:  # pragma: no cover - no media backend in this deployment
        return frame
    try:
        if isinstance(frame, av.VideoFrame):
            clone = av.VideoFrame.from_ndarray(
                frame.to_ndarray(format="yuv420p"), format="yuv420p"
            )
        elif isinstance(frame, av.AudioFrame):
            clone = av.AudioFrame.from_ndarray(
                frame.to_ndarray(), format=frame.format.name, layout=frame.layout.name
            )
            clone.sample_rate = frame.sample_rate
        else:
            return frame
        # The encoders timestamp their output from these, so the copy carries
        # them. A source frame need not have a time base (a test-built frame
        # does not), and assigning ``None`` raises rather than clearing it.
        clone.pts = frame.pts
        if frame.time_base is not None:
            clone.time_base = frame.time_base
    except Exception as exc:
        # A frame this cannot copy is still the encoder's input; the relay's
        # read loop must not end because one frame was uncopyable.
        logger.debug(f"Fan-out could not copy a {type(frame).__name__}: {exc}")
        return frame
    return clone


class FanoutView(_MediaStreamTrack):
    """One sender's private view of a shared :class:`TrackFanout`."""

    def __init__(self, fanout: "TrackFanout", queue: asyncio.Queue) -> None:
        super().__init__()
        self.fanout = fanout
        self._queue = queue
        self._id = str(uuid.uuid4())
        self._closed = False
        self._enabled = True

    @property
    def kind(self) -> str:
        return self.fanout.kind

    @property
    def id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return getattr(self.fanout.source, "label", "")

    @property
    def readyState(self) -> str:
        return "ended" if self._closed else "live"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def onended(self) -> Any:
        return getattr(self.fanout.source, "onended", None)

    @onended.setter
    def onended(self, handler: Any) -> None:
        try:
            self.fanout.source.onended = handler
        except Exception:
            pass

    def stop(self) -> None:
        """Detach this view only -- never the relay or the shared track."""
        self._closed = True
        self.fanout.unsubscribe(self)

    async def recv(self) -> Any:
        """Return the next fanned frame, or ``None`` at end of stream."""
        if self._closed:
            return None
        frame = await self._queue.get()
        # Tell the read loop this view is ready for more.
        self.fanout._signal_space()
        if frame is None:
            # End-of-stream sentinel: the relay is done.
            self._closed = True
            return None
        return frame


class TrackFanout:
    """Read ``source`` once and deliver every frame to every subscriber."""

    def __init__(self, source: Any, max_lag: int = DEFAULT_MAX_LAG) -> None:
        self.source = source
        self.kind = getattr(source, "kind", "unknown")
        self._max_lag = max(1, int(max_lag))
        self._queues: Dict[FanoutView, asyncio.Queue] = {}
        self._task: Optional[asyncio.Task] = None
        self._closed = False
        self._ended = False
        self._dropped = 0
        # Signalled by a view whenever it takes a frame, so the read loop can
        # pace itself to the subscribers instead of to the source.
        self._space = asyncio.Event()
        self._space.set()

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    @property
    def dropped_frames(self) -> int:
        """Frames shed because a view did not keep up (observability)."""
        return self._dropped

    @property
    def closed(self) -> bool:
        return self._closed

    def subscribe(self) -> FanoutView:
        """Create a view that receives every subsequent frame."""
        if self._closed:
            raise RuntimeError("fanout is closed")
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_lag)
        view = FanoutView(self, queue)
        self._queues[view] = queue
        if self._task is None or self._task.done():
            if not self._ended:
                self._task = asyncio.ensure_future(self._run())
        return view

    def unsubscribe(self, view: FanoutView) -> None:
        self._queues.pop(view, None)
        # Wake a paced read loop so it re-evaluates its (now smaller) view set.
        self._space.set()
        # With no subscribers left there is no reason to keep pulling (and
        # discarding) the source's frames; a later subscriber restarts it.
        if not self._queues and self._task is not None and not self._task.done():
            self._task.cancel()
            self._task = None

    def _has_space(self) -> bool:
        """Whether any view can accept another frame right now."""
        return any(queue.qsize() < queue.maxsize for queue in self._queues.values())

    def _signal_space(self) -> None:
        """A view took a frame; let a paced read loop continue."""
        self._space.set()

    async def _wait_for_space(self) -> None:
        """Block until at least one view can accept another frame.

        Pacing the read to the *fastest* subscriber is what stops a consumer
        that keeps up from losing frames. Reading as fast as the source
        produces only overruns the queues of views whose encoders have not
        drained them yet, so a view that is perfectly able to keep up sheds
        frames purely because the loop ran ahead of it.
        """
        if not self._queues or self._has_space():
            return
        self._space.clear()
        if self._has_space():
            # A view drained while we were clearing; do not wait on a flagged
            # event that was already consumed.
            self._space.set()
            return
        await self._space.wait()

    async def _run(self) -> None:
        cancelled = False
        try:
            while True:
                await self._wait_for_space()
                if self._closed:
                    break
                frame = await self.source.recv()
                if frame is None:
                    break
                self._offer(frame)
        except asyncio.CancelledError:
            # Cancelled because the last subscriber left, not because the
            # stream ended: a later subscriber must be able to restart the
            # read loop, so this must NOT be recorded as end-of-stream.
            cancelled = True
            raise
        except Exception as exc:  # a source failure ends the stream, not the room
            logger.debug(f"Fan-out source ended: {exc}")
        finally:
            if not cancelled and not self._closed:
                self._ended = True
                self._offer(None)

    def _offer(self, frame: Any) -> None:
        """Put ``frame`` (or the EOF sentinel) into every view's queue.

        With more than one subscriber each view gets its own copy of the frame:
        the views feed separate encoders, which run on the loop's executor at
        the same time, and one frame object shared between them crashed the
        process (see :func:`_private_frame`). A single subscriber has no one to
        race with, so it keeps the source's own frame and pays nothing.
        """
        views = list(self._queues.items())
        for view, queue in views:
            offered = frame
            if frame is not None and len(views) > 1:
                offered = _private_frame(frame)
            try:
                queue.put_nowait(offered)
            except asyncio.QueueFull:
                # Shed the oldest frame rather than block or grow: the view is
                # behind and stale media is worse than a dropped frame.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._dropped += 1
                try:
                    queue.put_nowait(offered)
                except asyncio.QueueFull:
                    pass

    async def aclose(self) -> None:
        """Stop reading and end every view."""
        self._closed = True
        self._ended = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._offer(None)
        self._queues.clear()
