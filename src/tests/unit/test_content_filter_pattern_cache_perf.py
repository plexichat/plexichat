"""Regex used by the per-message content filter is compiled once per word set.

``ContentFilterService._apply_user_filters`` used to escape, join and compile a
combined profanity/custom-word regex on *every* message.  The word list, not
the message, is the only input to that work, so a bounded LRU now memoizes the
compiled pattern by its frozen word set.  These tests pin the work (compile
call count) rather than flaky wall-clock numbers, then check the cache is
bounded and cannot cross user word sets.
"""

import re
import time
from unittest.mock import patch

import pytest

from src.core.messaging.services import content_filter as cf
from src.core.messaging.models import ContentFilter, FilterAction
from src.core.messaging.services.content_filter import ContentFilterService

pytestmark = [pytest.mark.unit]

PROFANITY_WORDS = {f"prof{i:03d}" for i in range(40)} | {"foo", "bad"}
CUSTOM_WORDS = [f"cust{i:03d}" for i in range(10)] + ["bar"]
CONTENT = "a clean foo prof001 cust001 message " * 4


def _service() -> ContentFilterService:
    svc = object.__new__(ContentFilterService)
    svc._profanity_words = set(PROFANITY_WORDS)
    return svc


def _filter(user_id: int, words, action=FilterAction.CENSOR) -> ContentFilter:
    return ContentFilter(
        user_id=user_id,
        profanity_filter=True,
        custom_blocked_words=list(words),
        filter_action=action,
    )


@pytest.fixture(autouse=True)
def _clear_pattern_cache():
    cf._USER_FILTER_PATTERN_CACHE.clear()
    yield
    cf._USER_FILTER_PATTERN_CACHE.clear()


def test_repeated_messages_compile_pattern_once():
    svc, uf = _service(), _filter(1, CUSTOM_WORDS)
    real_compile = re.compile
    calls = []

    def counting(*args, **kwargs):
        calls.append(args[0] if args else None)
        return real_compile(*args, **kwargs)

    with patch.object(cf.re, "compile", counting):
        svc._apply_user_filters(CONTENT, uf)
        assert len(calls) == 1, "first message compiles the word set"
        for _ in range(25):
            svc._apply_user_filters(CONTENT, uf)
        assert len(calls) == 1, "later messages reuse the compiled pattern"


def test_warm_cache_skips_deliberate_compile_cost():
    svc, uf = _service(), _filter(1, CUSTOM_WORDS)
    real_compile = re.compile
    delay = 0.002

    def slow_compile(*args, **kwargs):
        time.sleep(delay)
        return real_compile(*args, **kwargs)

    with patch.object(cf.re, "compile", slow_compile):
        t0 = time.perf_counter()
        svc._apply_user_filters(CONTENT, uf)  # cold: pays the injected delay
        cold = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(10):
            svc._apply_user_filters(CONTENT, uf)  # warm: never recompiles
        warm = time.perf_counter() - t0

    assert cold >= delay, "cold path must compile"
    assert warm < delay, "ten warm messages must cost less than one compile"


def test_distinct_word_sets_are_isolated_per_user():
    svc = _service()
    uf_a = _filter(1, ["alpha"])
    uf_b = _filter(2, ["beta"])

    out_a, words_a = svc._apply_user_filters("alpha beta", uf_a)
    out_b, words_b = svc._apply_user_filters("alpha beta", uf_b)

    # Each user's custom word is censored only for that user.
    assert "alpha" not in out_a and "beta" in out_a
    assert "beta" not in out_b and "alpha" in out_b
    assert set(words_a) == {"alpha"}
    assert set(words_b) == {"beta"}
    assert len(cf._USER_FILTER_PATTERN_CACHE) == 2


@pytest.mark.parametrize(
    "action",
    [FilterAction.CENSOR, FilterAction.SPOILER, FilterAction.BLOCK, FilterAction.WARN],
)
def test_cached_pattern_preserves_output_for_every_action(action):
    svc = _service()
    uf = _filter(7, CUSTOM_WORDS, action)
    cf._USER_FILTER_PATTERN_CACHE.clear()
    fresh, fresh_words = svc._apply_user_filters(CONTENT, uf)
    cached, cached_words = svc._apply_user_filters(CONTENT, uf)
    assert cached == fresh
    assert set(cached_words) == set(fresh_words)


def test_pattern_cache_stays_bounded_under_rotating_word_sets():
    svc = _service()
    stats = cf._USER_FILTER_PATTERN_CACHE.stats()
    assert 1 <= stats["max_entries"] <= 500
    assert stats["max_bytes"] >= 1
    assert cf._USER_FILTER_PATTERN_CACHE._ttl > 0

    for i in range(stats["max_entries"] + 20):
        svc._apply_user_filters(CONTENT, _filter(1000 + i, [f"unique{i}"]))

    assert len(cf._USER_FILTER_PATTERN_CACHE) <= stats["max_entries"]
