"""Performance regression guards for media streaming and result reuse.

These tests pin the *cost* properties established by the streaming and cache
work rather than just the outputs: reads stay bounded, range skipping happens
via seek (or a zero-copy view on push streams), decode/transcode work is not
repeated for identical content, and the memo table cannot grow without bound.
"""

import io

import pytest

from src.core.media._result_cache import ResultCache
from src.core.media._streaming import DEFAULT_CHUNK_SIZE, iter_bounded_chunks
from src.core.media.compression import (
    CompressionQuality,
    CompressionResult,
    VideoCompressor,
)
from src.core.media.processing import ImageProcessor


class _BoundedFile:
    """File-like stub that refuses oversized reads and records seeks."""

    def __init__(self, data):
        self._data = data
        self._pos = 0
        self.seeks = []
        self.closed = False

    def read(self, size=-1):
        assert 0 < size <= DEFAULT_CHUNK_SIZE, f"unbounded read of {size} bytes"
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def seek(self, offset):
        self.seeks.append(offset)
        self._pos = offset

    def tell(self):
        return self._pos

    def close(self):
        self.closed = True


def test_streaming_never_reads_more_than_one_bounded_chunk():
    payload = bytes(range(256)) * 800  # 200 KiB
    stream = _BoundedFile(payload)

    parts = list(iter_bounded_chunks(stream, skip=0, limit=len(payload)))

    assert b"".join(parts) == payload
    assert len(parts) == (len(payload) + DEFAULT_CHUNK_SIZE - 1) // DEFAULT_CHUNK_SIZE
    assert all(len(part) <= DEFAULT_CHUNK_SIZE for part in parts)
    assert stream.closed is True


def test_range_serving_seeks_once_instead_of_reading_the_prefix():
    payload = b"x" * (DEFAULT_CHUNK_SIZE * 4)
    stream = _BoundedFile(payload)

    parts = list(iter_bounded_chunks(stream, skip=1000, limit=4096))

    assert stream.seeks == [1000]
    assert b"".join(parts) == payload[1000:5096]
    assert all(len(part) <= DEFAULT_CHUNK_SIZE for part in parts)


def test_push_stream_range_boundary_stays_a_view_of_the_original_bytes():
    chunk = b"0123456789" * 100
    source = iter([chunk])

    parts = list(iter_bounded_chunks(source, skip=15, limit=40))

    assert b"".join(bytes(part) for part in parts) == chunk[15:55]
    # A bytes slice would allocate a second 1000-byte payload; a memoryview
    # only carries an offset/length.
    assert isinstance(parts[0], memoryview)
    assert parts[0].obj is chunk


def _png(color):
    Image = pytest.importorskip("PIL.Image")
    image = Image.new("RGB", (16, 16), color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_repeated_thumbnail_request_skips_the_image_decode(monkeypatch):
    data = _png("red")
    processor = ImageProcessor()
    decodes = []
    original = processor._validate_image_dimensions

    def counting_validate(img, compressed_bytes=0):
        decodes.append(img)
        return original(img, compressed_bytes)

    monkeypatch.setattr(processor, "_validate_image_dimensions", counting_validate)

    first = processor.create_thumbnail(data, 8)
    assert len(decodes) == 1
    assert processor.create_thumbnail(data, 8) == first
    assert len(decodes) == 1  # cache hit: no second decode/encode


def test_thumbnail_memo_stays_bounded_across_distinct_content():
    pytest.importorskip("PIL.Image")
    processor = ImageProcessor()
    processor._thumbnail_cache = ResultCache(
        maxsize=4, max_bytes=10 * 1024 * 1024, sizer=lambda value: len(value[0])
    )

    results = []
    for red in (10, 20, 30, 40, 50, 60, 70, 80):
        encoded = _png((red, 0, 0))
        results.append(processor.create_thumbnail(encoded, 8))

    assert all(result[0] for result in results)
    assert len(processor._thumbnail_cache._cache) <= 4
    assert processor._thumbnail_cache.stats()["evictions"] >= 4
    # Most-recent result survives eviction and is still reusable.
    assert processor.create_thumbnail(_png((80, 0, 0)), 8) == results[-1]


def test_transcode_memo_is_keyed_by_content_and_quality(monkeypatch):
    compressor = VideoCompressor()
    monkeypatch.setattr(compressor, "is_available", lambda: True)
    calls = []

    def fake_transcode(data, quality=None, codec=None):
        calls.append((data, quality))
        return CompressionResult(success=True, data=b"encoded", original_size=len(data))

    monkeypatch.setattr(compressor, "_compress_uncached", fake_transcode)

    compressor.compress(b"clip", quality=CompressionQuality.LOW)
    compressor.compress(b"clip", quality=CompressionQuality.LOW)
    assert len(calls) == 1

    compressor.compress(b"clip", quality=CompressionQuality.HIGH)
    assert len(calls) == 2  # quality is part of the cache key

    compressor.compress(b"different-clip", quality=CompressionQuality.LOW)
    assert len(calls) == 3  # content hash is part of the cache key
