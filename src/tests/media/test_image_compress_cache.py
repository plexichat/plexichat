"""Regression: image re-compression reuses a bounded, content-hash-keyed result.

Before: ``ImageCompressor.compress`` re-decoded and re-encoded the image on
every call, so repeated uploads of the same payload paid the full Pillow cost
again and again. After: a successful result is memoized under the content hash
plus every parameter that changes the output bytes, with hard bounds on the
number of entries, retained bytes and concurrent computations. The bomb and
alpha-flattening guards are untouched and still run on every cache miss.
"""

import io

import pytest

from src.core.media import compression
from src.core.media._result_cache import ResultCache, content_hash
from src.core.media.compression import (
    CompressionQuality,
    ImageCompressor,
    ImageFormat,
)


def _media_config(preserve_original=True, max_pixels=178_956_970, **image_overrides):
    image = {
        "format": "webp",
        "quality": 85,
        "max_dimension": 4096,
        "max_pixels": max_pixels,
    }
    image.update(image_overrides)
    return {
        "compression": {
            "enabled": True,
            "preserve_original": preserve_original,
            "image": image,
        }
    }


def _compressor(monkeypatch, **config_kwargs):
    """Build an enabled ImageCompressor under a controlled media config."""
    media = _media_config(**config_kwargs)
    real_get = compression.config.get
    monkeypatch.setattr(
        compression.config,
        "get",
        lambda key, default=None: media if key == "media" else real_get(key, default),
    )
    compressor = ImageCompressor()
    assert compressor.is_available()
    return compressor


def _png(color=(120, 40, 200), size=(32, 32), mode="RGB"):
    Image = pytest.importorskip("PIL.Image")
    image = Image.new(mode, size, color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _count_recompressions(compressor, monkeypatch):
    """Count uncached decodes via the bomb guard that runs on every one."""
    calls = []
    original = compressor._reject_decompression_bomb

    def counting(img):
        calls.append(img)
        return original(img)

    monkeypatch.setattr(compressor, "_reject_decompression_bomb", counting)
    return calls


def test_identical_compress_output_is_reused(monkeypatch):
    """Repeated (bytes, settings) produce identical output and one computation."""
    data = _png()
    compressor = _compressor(monkeypatch)
    calls = _count_recompressions(compressor, monkeypatch)

    first = compressor.compress(data, CompressionQuality.MEDIUM, ImageFormat.WEBP)
    second = compressor.compress(data, CompressionQuality.MEDIUM, ImageFormat.WEBP)

    assert first.success and second.success
    assert first.data == second.data
    assert first.original_size == second.original_size
    assert first.compressed_size == second.compressed_size
    assert first.format == second.format
    assert len(calls) == 1  # only the first call paid for a Pillow decode/encode
    assert compressor._compress_cache.stats()["computes"] == 1
    assert compressor._compress_cache.stats()["hits"] == 1


def test_transform_params_are_part_of_the_cache_key(monkeypatch):
    """Quality, format and config changes invalidate the memoized result."""
    data = _png()
    compressor = _compressor(monkeypatch)
    calls = _count_recompressions(compressor, monkeypatch)

    compressor.compress(data, CompressionQuality.MEDIUM, ImageFormat.WEBP)
    compressor.compress(data, CompressionQuality.MEDIUM, ImageFormat.WEBP)
    assert len(calls) == 1

    compressor.compress(data, CompressionQuality.HIGH, ImageFormat.WEBP)
    assert len(calls) == 2  # quality is in the key

    compressor.compress(data, CompressionQuality.MEDIUM, ImageFormat.PNG)
    assert len(calls) == 3  # output format is in the key

    key = compressor._compress_key(data, CompressionQuality.MEDIUM, ImageFormat.WEBP)
    assert key[1] == content_hash(data)
    assert key[2] == CompressionQuality.MEDIUM.value
    assert key[3] == ImageFormat.WEBP.value


def test_compress_memo_is_bounded_across_distinct_content(monkeypatch):
    """Distinct uploads cannot grow the cache without limit."""
    compressor = _compressor(monkeypatch)
    compressor._compress_cache = ResultCache(
        maxsize=2,
        max_bytes=4 * 1024 * 1024,
        sizer=lambda result: max(1, len(result.data or b"")),
    )

    results = []
    for red in (10, 20, 30, 40, 50, 60):
        results.append(compressor.compress(_png((red, 0, 0))))

    assert all(result.success for result in results)
    assert len(compressor._compress_cache._cache) <= 2
    assert compressor._compress_cache.stats()["evictions"] >= 4
    # The most recent result survives eviction and is still reusable.
    assert compressor.compress(_png((60, 0, 0))).data == results[-1].data


def test_decompression_bomb_is_rejected_and_never_cached(monkeypatch):
    """A rejected bomb stays a failure on every call and leaves no cache entry."""
    compressor = _compressor(monkeypatch, max_pixels=100)  # 32x32 = 1024 px > 100
    calls = _count_recompressions(compressor, monkeypatch)
    data = _png()

    first = compressor.compress(data)
    second = compressor.compress(data)

    assert first.success is False and second.success is False
    assert "decompression" in (first.error or "").lower()
    assert "decompression" in (second.error or "").lower()
    # Validation is re-run rather than a failure being memoized.
    assert len(calls) == 2
    key = compressor._compress_key(data, None, None)
    assert compressor._compress_cache.get(key) is None


def test_jpeg_alpha_flatten_guard_survives_a_cache_hit(monkeypatch):
    """Flattening transparent sources onto white is identical on a cache hit."""
    Image = pytest.importorskip("PIL.Image")
    compressor = _compressor(monkeypatch)
    calls = _count_recompressions(compressor, monkeypatch)

    # Fully transparent RGBA pixels would be written as solid black if the
    # alpha-flattening guard were skipped after the first compression.
    data = _png((0, 0, 0, 0), mode="RGBA")

    first = compressor.compress(data, output_format=ImageFormat.JPEG)
    second = compressor.compress(data, output_format=ImageFormat.JPEG)

    assert first.success and second.success
    assert first.data == second.data
    assert len(calls) == 1
    decoded = Image.open(io.BytesIO(second.data))
    assert decoded.mode == "RGB"
    corner = decoded.convert("RGB").getpixel((0, 0))
    assert all(channel >= 250 for channel in corner), corner
