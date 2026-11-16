"""Bounded, content-hash-keyed reuse of thumbnail/transcode work.

Before: repeated calls redid the Pillow decode/encode or spawned a fresh
ffmpeg process. After: a bounded LRU keyed by SHA-256 content hash serves
repeats, coalesces concurrent identical work, and caps in-flight computations.
"""

import threading
import time

import pytest

from src.core.media._result_cache import ResultCache, content_hash
from src.core.media.compression import CompressionResult, VideoCompressor
from src.core.media.processing import ImageProcessor


def test_content_hash_is_stable_and_content_sensitive():
    assert content_hash(b"abc") == content_hash(b"abc")
    assert content_hash(b"abc") != content_hash(b"abd")


def test_lru_evicts_by_count_and_byte_budget():
    cache = ResultCache(maxsize=2, max_bytes=10, sizer=len)
    cache.put("a", b"12345")
    cache.put("b", b"12345")
    assert cache.get("a") == b"12345"

    cache.put("c", b"12345")  # 15 bytes > 10: evicts least-recently-used "b"
    assert cache.get("b") is None
    assert cache.get("c") == b"12345"
    assert len(cache._cache) == 2
    assert cache.stats()["evictions"] >= 1


def test_oversized_result_is_not_cached():
    cache = ResultCache(maxsize=8, max_bytes=4, sizer=len)
    cache.put("big", b"x" * 100)
    assert cache.get("big") is None
    assert cache.stats()["not_cached"] == 1


def test_get_or_compute_coalesces_identical_calls():
    cache = ResultCache()
    calls = []
    barrier = threading.Barrier(6)
    results = []

    def worker():
        barrier.wait()
        results.append(cache.get_or_compute("key", lambda: calls.append(1) or b"value"))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [b"value"] * 6
    assert len(calls) == 1
    assert cache.stats()["computes"] == 1


def test_in_flight_computations_are_bounded():
    limit = 2
    cache = ResultCache(max_inflight=limit)
    active = 0
    peak = 0
    guard = threading.Lock()
    barrier = threading.Barrier(6)

    def compute(key):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.01)
            return key
        finally:
            with guard:
                active -= 1

    def worker(i):
        barrier.wait()
        assert cache.get_or_compute(("k", i), lambda: compute(i)) == i

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak <= limit
    assert peak >= 1


def test_thumbnail_is_computed_once_per_content_and_size():
    Image = pytest.importorskip("PIL.Image")
    import io

    image = Image.new("RGB", (32, 32), color="red")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()

    processor = ImageProcessor()
    first = processor.create_thumbnail(data, 16)
    assert processor._thumbnail_cache.stats()["computes"] == 1

    second = processor.create_thumbnail(data, 16)
    assert second == first
    assert processor._thumbnail_cache.stats()["computes"] == 1

    # A different size is a different key and must still be computed.
    processor.create_thumbnail(data, 8)
    assert processor._thumbnail_cache.stats()["computes"] == 2


def test_create_thumbnails_reuses_cached_sizes():
    Image = pytest.importorskip("PIL.Image")
    import io

    image = Image.new("RGB", (32, 32), color="blue")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()

    processor = ImageProcessor()
    sizes = [8, 16]
    first = processor.create_thumbnails(data, sizes)
    assert set(first) == set(sizes)
    assert processor._thumbnail_cache.stats()["computes"] == 2

    second = processor.create_thumbnails(data, sizes)
    assert second == first
    assert processor._thumbnail_cache.stats()["computes"] == 2


def test_transcode_result_is_reused_for_identical_input(monkeypatch):
    compressor = VideoCompressor()
    monkeypatch.setattr(compressor, "is_available", lambda: True)
    calls = []

    def fake_compress(data, quality=None, codec=None):
        calls.append(data)
        return CompressionResult(success=True, data=b"encoded", original_size=len(data))

    monkeypatch.setattr(compressor, "_compress_uncached", fake_compress)

    first = compressor.compress(b"video-bytes")
    second = compressor.compress(b"video-bytes")

    assert first.success and second.success
    assert calls == [b"video-bytes"]
    assert compressor._transcode_cache.stats()["computes"] == 1


def test_failed_transcode_is_not_cached(monkeypatch):
    compressor = VideoCompressor()
    monkeypatch.setattr(compressor, "is_available", lambda: True)
    calls = []

    def failing(data, quality=None, codec=None):
        calls.append(data)
        return CompressionResult(success=False, error="boom")

    monkeypatch.setattr(compressor, "_compress_uncached", failing)

    assert compressor.compress(b"bad").success is False
    assert compressor.compress(b"bad").success is False
    assert len(calls) == 2
    assert (
        compressor._transcode_cache.get(
            (
                "transcode",
                content_hash(b"bad"),
                None,
                None,
                compressor._ffmpeg_path,
                tuple((k, str(v)) for k, v in sorted(compressor._config.items())),
            )
        )
        is None
    )
