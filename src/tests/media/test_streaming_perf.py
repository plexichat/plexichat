"""Bounded, zero-copy media response streaming.

Before: the attachment route sliced every generator chunk with ``chunk[a:b]``
(a full byte copy per range boundary), hard-coded a 64 KiB read, and only
closed seekable files when the whole body was consumed. After: a single
bounded iterator lives in ``src.core.media._streaming``, seekable files are
yielded exactly as ``read`` returns them, and range boundaries on push streams
are sliced through a ``memoryview``.
"""

import pytest

from src.core.media._streaming import (
    DEFAULT_CHUNK_SIZE,
    MAX_CHUNK_SIZE,
    MIN_CHUNK_SIZE,
    bounded_chunk_size,
    is_seekable_stream,
    iter_bounded_chunks,
)
from src.core.security.hardening import HardeningViolation


class _SeekableStub:
    """Minimal seekable stream that hands back pre-built byte objects."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._pos = 0
        self.reads = []
        self.closed = False

    def read(self, size=-1):
        self.reads.append(size)
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if size >= 0 and len(chunk) > size:
            self._chunks.insert(0, chunk[size:])
            chunk = chunk[:size]
        self._pos += len(chunk)
        return chunk

    def seek(self, offset):
        self._pos = offset

    def tell(self):
        return self._pos

    def close(self):
        self.closed = True


def test_chunk_size_is_bounded():
    assert bounded_chunk_size() == DEFAULT_CHUNK_SIZE
    assert bounded_chunk_size(8192) == 8192
    assert bounded_chunk_size(MIN_CHUNK_SIZE) == MIN_CHUNK_SIZE
    assert bounded_chunk_size(MAX_CHUNK_SIZE) == MAX_CHUNK_SIZE
    for bad in (1, MAX_CHUNK_SIZE + 1, "abc", None.__class__):
        with pytest.raises((HardeningViolation, TypeError)):
            bounded_chunk_size(bad)


def test_seekable_files_are_read_in_bounded_zero_copy_chunks():
    first = b"A" * MIN_CHUNK_SIZE
    second = b"B" * MIN_CHUNK_SIZE
    stream = _SeekableStub([first, second])

    parts = list(
        iter_bounded_chunks(
            stream, skip=0, limit=MIN_CHUNK_SIZE + 100, chunk_size=MIN_CHUNK_SIZE
        )
    )

    assert [bytes(p) for p in parts] == [first, second[:100]]
    assert all(len(p) <= MIN_CHUNK_SIZE for p in parts)
    # The bytes object returned by read() is yielded verbatim, never sliced.
    assert parts[0] is first
    # Every read is capped by both the chunk size and the remaining limit.
    assert stream.reads == [MIN_CHUNK_SIZE, 100]


def test_seekable_stream_is_closed_when_generator_is_abandoned():
    stream = _SeekableStub([b"x" * MIN_CHUNK_SIZE, b"y" * MIN_CHUNK_SIZE])
    gen = iter_bounded_chunks(stream, skip=0, limit=2 * MIN_CHUNK_SIZE)

    assert next(gen) == b"x" * MIN_CHUNK_SIZE
    assert stream.closed is False
    gen.close()
    assert stream.closed is True


def test_range_boundary_on_push_stream_is_a_memoryview():
    a = b"0123456789"
    b = b"abcdefghij"
    c = b"ABCDEFGHIJ"
    yielded = []

    def source():
        yielded.append(a)
        yield a
        yielded.append(b)
        yield b
        yielded.append(c)
        yield c

    stream = source()
    parts = list(iter_bounded_chunks(stream, skip=15, limit=20))

    assert b"".join(bytes(p) for p in parts) == b"fghijABCDEFGHIJ"
    # The straddling chunk is a view over the original bytes (no copy), while
    # fully-consumed chunks are yielded as-is.
    assert isinstance(parts[0], memoryview)
    assert parts[0].obj is b
    assert parts[-1] is c


def test_push_stream_stops_at_limit_and_closes():
    class ClosingSource:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            return b"z" * 8

        def close(self):
            self.closed = True

    stream = ClosingSource()
    parts = list(iter_bounded_chunks(stream, skip=0, limit=10))

    assert [len(bytes(p)) for p in parts] == [8, 2]
    assert stream.closed is True


def test_zero_limit_closes_without_reading():
    stream = _SeekableStub([b"data"])
    assert list(iter_bounded_chunks(stream, skip=0, limit=0)) == []
    assert stream.closed is True
    assert stream.reads == []


def test_is_seekable_stream():
    assert is_seekable_stream(_SeekableStub([])) is True
    assert is_seekable_stream(iter([b"a"])) is False
