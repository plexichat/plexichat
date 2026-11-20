"""Regressions for the compiled-regex caching in the content filter.

The NSFW patterns and the blocked-word alternation used to be recompiled (or
re-looked-up in ``re``'s internal cache) on every validated message. These
tests pin that repeated filters become cache hits, that the cache stays
bounded under attacker-chosen word sets, and that two users with different
word sets never share a compiled pattern (no cross-user leak of behaviour).
"""

import pytest

from src.core.messaging import content as content_mod
from src.core.messaging.content import get_processor
from src.core.messaging.models import ContentFilter, FilterAction


def _filter(words):
    return ContentFilter(
        user_id=1,
        custom_blocked_words=words,
        filter_action=FilterAction.CENSOR,
    )


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Start each test with an empty compiled-pattern cache."""
    content_mod._compile_word_pattern.cache_clear()
    yield
    content_mod._compile_word_pattern.cache_clear()


@pytest.mark.messaging
class TestNsfwPatternsCompiledOnce:
    def test_nsfw_patterns_are_compiled_at_load(self):
        processor = get_processor()
        assert processor._nsfw_regexes
        assert len(processor._nsfw_regexes) == len(processor._nsfw_patterns)
        for regex in processor._nsfw_regexes:
            assert hasattr(regex, "search")
        # The uncompiled strings are still available for callers that inspect
        # config; the compiled forms are what validation now uses.
        assert processor._nsfw_patterns[0] == r"\bnsfw\b"


@pytest.mark.messaging
class TestCompiledPatternCache:
    def test_repeated_word_set_is_a_cache_hit(self):
        processor = get_processor()
        user_filter = _filter(["secret", "badword"])
        content_mod._compile_word_pattern.cache_clear()

        for _ in range(4):
            processor._apply_user_filters("a secret and badword here", user_filter)

        info = content_mod._compile_word_pattern.cache_info()
        assert info.misses == 1
        assert info.hits == 3

    def test_distinct_word_sets_do_not_share_a_pattern(self):
        processor = get_processor()
        content_mod._compile_word_pattern.cache_clear()

        processor._apply_user_filters("a secret here", _filter(["secret"]))
        assert content_mod._compile_word_pattern.cache_info().currsize == 1

        # A different user's word set is a distinct key: no shared pattern,
        # hence no way for one user's filter to alter another's result.
        processor._apply_user_filters("a secret here", _filter(["secret", "other"]))
        info = content_mod._compile_word_pattern.cache_info()
        assert info.currsize == 2
        assert info.misses == 2

    def test_filter_result_is_unchanged_by_caching(self):
        processor = get_processor()
        user_filter = _filter(["bad", "badword"])

        first = processor._apply_user_filters("a badword b", user_filter)
        # Second call reads from the cache, not a freshly compiled pattern.
        second = processor._apply_user_filters("a badword b", user_filter)

        assert first == second == ("a ******* b", ["badword"])

    def test_cache_is_bounded_under_word_set_churn(self):
        processor = get_processor()
        content_mod._compile_word_pattern.cache_clear()
        maxsize = content_mod._compile_word_pattern.cache_info().maxsize

        # Far more distinct word sets than the entry cap.
        for i in range(maxsize + 40):
            processor._apply_user_filters("nothing to see", _filter([f"word{i}"]))

        info = content_mod._compile_word_pattern.cache_info()
        assert info.currsize <= maxsize
