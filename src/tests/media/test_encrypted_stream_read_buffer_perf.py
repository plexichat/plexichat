"""Performance regression for buffered reads of encrypted media streams.

``_GeneratorStream.read(n)`` is used to serve large encrypted files.  It used
to grow its byte buffer with ``self._buffer += chunk`` for every decrypted
chunk.  An attribute target keeps a live reference, so CPython cannot do its
in-place resize optimization and every append copies the whole buffer --
quadratic in the number of chunks.  The fix accumulates chunks in a list and
joins once, which is linear and produces the same bytes.
"""

import gc
import time

import pytest

from src.core.media.storage.encrypted import _GeneratorStream

CHUNK = b"x" * 64
N_CHUNKS = 4000


def _generator(n):
    return (CHUNK for _ in range(n))


def _time_best(fn, reps=5):
    """Best of several runs to filter out scheduler noise."""
    best = float("inf")
    output = None
    for _ in range(reps):
        start = time.perf_counter()
        output = fn()
        best = min(best, time.perf_counter() - start)
    return best, output


@pytest.mark.media
def test_generator_stream_big_read_is_linear():
    total = len(CHUNK) * N_CHUNKS

    def big_read():
        stream = _GeneratorStream(_generator(N_CHUNKS))
        return stream.read(total)

    def chunked_reads():
        # Same bytes, consumed one chunk at a time: a linear-time control.
        stream = _GeneratorStream(_generator(N_CHUNKS))
        return b"".join(stream.read(len(CHUNK)) for _ in range(N_CHUNKS))

    gc.disable()
    try:
        big_time, big_output = _time_best(big_read)
        small_time, small_output = _time_best(chunked_reads)
    finally:
        gc.enable()

    # The optimization must be byte-for-byte transparent.
    assert big_output == small_output == CHUNK * N_CHUNKS

    # Before the fix, the big read took >10x the chunked reads because of
    # quadratic concatenation.  A single join keeps it comparable.
    assert big_time <= small_time * 5, (
        f"big read {big_time:.4f}s vs chunked {small_time:.4f}s "
        f"(ratio {big_time / small_time:.1f}x): quadratic concatenation regression"
    )
