"""Media track helpers for the aiortc SFU adapter."""

from __future__ import annotations

import sys as _sys
import time as _time_mod
import uuid
from typing import Any

from .constants import DEGRADED_VIDEO_FPS

_pkg = _sys.modules[__name__.rsplit(".", 1)[0]]
time = getattr(_pkg, "time", _time_mod)


class BoundedVideoTrack:
    """A frame-rate bounded view of a video track, for a degraded peer.

    ``stop()`` deliberately does NOT stop the wrapped source: that same track
    is forwarded to every other peer in the room.
    """

    def __init__(self, source: Any, max_fps: float = DEGRADED_VIDEO_FPS) -> None:
        self._source = source
        self._min_interval = 1.0 / max(1.0, float(max_fps))
        self._last_sent = 0.0
        self._ended = False
        self._id = str(uuid.uuid4())
        self._enabled = True

    @property
    def source(self) -> Any:
        """The bounded track itself; what a restore re-attaches."""
        return self._source

    @property
    def kind(self) -> str:
        return getattr(self._source, "kind", "video")

    @property
    def id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return getattr(self._source, "label", "")

    @property
    def readyState(self) -> str:
        return "ended" if self._ended else "live"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def stop(self) -> None:
        """End this view only -- never the shared track underneath it."""
        self._ended = True

    async def recv(self) -> Any:
        """Return the next frame the bounded rate allows, dropping the rest."""
        while not self._ended:
            frame = await self._source.recv()
            if frame is None:
                return None
            now = time.monotonic()
            if now - self._last_sent >= self._min_interval:
                self._last_sent = now
                return frame
        return None


def _same_sender_track(a: Any, b: Any) -> bool:
    """Whether ``a`` is a view of the produced track ``b``."""
    if a is b:
        return True
    relay = getattr(a, "fanout", None)
    return relay is not None and getattr(relay, "source", None) is b


__all__ = ["BoundedVideoTrack", "_same_sender_track"]
