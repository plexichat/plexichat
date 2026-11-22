"""filter_ice_urls must not materialize an unbounded URL list to keep a prefix."""

from src.core.voice.boundaries import (
    DEFAULT_PUBLIC_STUN_URLS,
    MAX_ICE_URLS_PER_SERVER,
    filter_ice_urls,
)

_GOOD = "stun:good.example.com:3478"
_BAD = "javascript:alert(1)"


def _counting_urls(count, value=_GOOD):
    """Yield ``count`` URLs, recording how many the caller actually pulled."""
    state = {"pulled": 0}

    def gen():
        for _ in range(count):
            state["pulled"] += 1
            yield value

    return gen(), state


def test_filter_stops_iterating_once_cap_is_reached():
    """Only the first MAX_ICE_URLS_PER_SERVER valid URLs can ever be returned.

    Reading the rest of the iterable is pure waste: before the fix a single
    oversized STUN config validated (and held) every URL before slicing.
    """
    gen, state = _counting_urls(100_000)
    result = filter_ice_urls(gen)

    assert result == [_GOOD] * MAX_ICE_URLS_PER_SERVER
    assert state["pulled"] == MAX_ICE_URLS_PER_SERVER


def test_filter_preserves_output_with_interleaved_invalid_urls():
    """Early exit keeps the exact first-N valid URLs, not a different prefix."""
    urls = [_BAD, "stun:a.example:3478", _BAD, "stun:b.example:3478"]
    urls += [f"stun:{i}.example:3478" for i in range(20)]
    urls += [_BAD, "stun:last.example:3478"]

    expected = [
        "stun:a.example:3478",
        "stun:b.example:3478",
    ] + [f"stun:{i}.example:3478" for i in range(20)]
    expected = expected[:MAX_ICE_URLS_PER_SERVER]

    assert filter_ice_urls(urls) == expected


def test_filter_scans_all_when_fewer_than_cap_and_falls_back():
    """With no valid URL (or too few to cap) behavior is unchanged."""
    assert filter_ice_urls([_BAD, _BAD]) == DEFAULT_PUBLIC_STUN_URLS
    gen, state = _counting_urls(3, value=_BAD)
    assert filter_ice_urls(gen) == DEFAULT_PUBLIC_STUN_URLS
    assert state["pulled"] == 3
