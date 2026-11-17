"""
Bounded, low-copy response streaming helpers.

Attachment responses used to inline an ad-hoc slicing generator inside the
route: every chunk was sliced with ``chunk[a:b]`` (a full byte copy), the
magic ``65536`` was hard-coded, and a seekable file was only closed on the
happy path.  This module centralises that logic so it can be reused and
regression-tested:

* a single bounded chunk size (``DEFAULT_CHUNK_SIZE``) is used everywhere;
* seekable file streams are read directly -- the bytes returned by ``read``
  are yielded as-is (no intermediate copy);
* for push/iterator streams the *skip* boundary is sliced with a
  ``memoryview``, so the bytes are never copied just to drop a prefix;
* the source stream is always closed, including when the client disconnects
  and the generator is closed early.
"""

from __future__ import annotations

import inspect
from typing import Any, Iterator, Optional, Union

from src.core.security.hardening import bounded_int

# 64 KiB keeps the syscall count low without pinning a whole large attachment
# in memory. The clamp means even a misconfigured caller cannot ask for a
# unbounded read.
DEFAULT_CHUNK_SIZE = 64 * 1024
MIN_CHUNK_SIZE = 4 * 1024
MAX_CHUNK_SIZE = 1024 * 1024

# Keep this aligned with Starlette's ``Content`` (str | bytes | memoryview) so
# the chunks can be handed straight to ``StreamingResponse``. ``bytearray`` is
# deliberately excluded: Starlette does not accept it as a response body.
Chunk = Union[bytes, memoryview]


def bounded_chunk_size(requested: Optional[object] = None) -> int:
    """Return a read chunk size clamped to ``[MIN_CHUNK_SIZE, MAX_CHUNK_SIZE]``."""
    if requested is None:
        # DEFAULT_CHUNK_SIZE is a module constant already clamped to the
        # allowed range; skip the validation call on every streamed response.
        return DEFAULT_CHUNK_SIZE
    return bounded_int(
        requested,
        "media response chunk size",
        minimum=MIN_CHUNK_SIZE,
        maximum=MAX_CHUNK_SIZE,
    )


def is_seekable_stream(stream: Any) -> bool:
    """Whether ``stream`` supports the seek/tell pair needed for byte ranges."""
    return (
        not inspect.isgenerator(stream)
        and hasattr(stream, "read")
        and hasattr(stream, "seek")
        and hasattr(stream, "tell")
    )


def _close_stream(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # pragma: no cover - defensive cleanup
            pass


def iter_bounded_chunks(
    stream: Any,
    skip: int,
    limit: int,
    chunk_size: Optional[object] = None,
) -> Iterator[Chunk]:
    """Yield at most ``limit`` bytes from ``stream`` starting at ``skip``.

    The iterator is deliberately synchronous: callers hand it to
    ``starlette.concurrency.iterate_in_threadpool`` so all the counting and
    slicing happens off the event loop. Two paths are supported:

    1. Seekable file-like streams: seek once, then ``read(min(chunk, remaining))``
       and yield the result unchanged. ``read`` already returns a fresh
       ``bytes`` object, so there is nothing to copy.
    2. Push/iterator streams: walk chunks until the requested offset is
       reached. The one chunk straddling the offset is sliced through a
       ``memoryview`` (a view, not a copy) and every later chunk is yielded
       untouched.

    ``limit <= 0`` yields nothing. The stream is closed on every exit path so
    a disconnected client cannot leak an open file descriptor.
    """
    size = bounded_chunk_size(chunk_size)
    if limit <= 0:
        _close_stream(stream)
        return

    skip = max(0, int(skip))
    limit = int(limit)

    if is_seekable_stream(stream):
        try:
            if skip:
                stream.seek(skip)
            remaining = limit
            while remaining > 0:
                chunk = stream.read(min(size, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)
        finally:
            _close_stream(stream)
        return

    # Iterator / push stream. We cannot seek, so walk until the offset is
    # consumed. Count in a local to keep the event-loop-facing generator tiny.
    consumed = 0
    yielded = 0
    try:
        for chunk in stream:
            if not chunk:
                continue
            chunk_len = len(chunk)
            if consumed + chunk_len <= skip:
                consumed += chunk_len
                continue

            start = max(0, skip - consumed)
            end = min(chunk_len, start + (limit - yielded))
            if start < end:
                # memoryview avoids re-allocating the payload for a mid-chunk
                # range boundary; it is still a bytes-like object for ASGI.
                part = (
                    memoryview(chunk)[start:end] if end - start < chunk_len else chunk
                )
                yield part
                yielded += end - start
            consumed += chunk_len
            if yielded >= limit:
                break
    finally:
        _close_stream(stream)


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "MAX_CHUNK_SIZE",
    "MIN_CHUNK_SIZE",
    "bounded_chunk_size",
    "is_seekable_stream",
    "iter_bounded_chunks",
]
