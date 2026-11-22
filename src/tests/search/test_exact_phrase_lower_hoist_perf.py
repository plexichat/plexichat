"""Call-count regression: exact-phrase case folding is invariant per result loop.

``FilterProcessor.apply_filters`` walks ``parsed_query.exact_phrases`` and, for
each phrase, filters the whole result list.  The phrase's lower-cased form does
not depend on the individual result, so it must be computed once per phrase
rather than once per (phrase, result) pair.
"""

import pytest

from src.core.search.models import ParsedQuery, MessageSearchResult
from src.core.search.query.filters import apply_filters


class CountingLowerStr(str):
    """A str that counts ``.lower()`` calls made on the phrase object itself."""

    def __new__(cls, value, counter):
        obj = super().__new__(cls, value)
        obj.lower_calls = counter
        return obj

    def lower(self):
        self.lower_calls[0] += 1
        return super().lower()


@pytest.mark.search
class TestExactPhraseLowerHoist:
    def test_phrase_lower_called_once_per_phrase_not_per_result(self):
        phrases = ["Alpha", "Bravo", "Charlie"]
        lower_calls = [0]
        spies = [CountingLowerStr(p, lower_calls) for p in phrases]

        results = [
            MessageSearchResult(
                id=i,
                message_id=i,
                content="alpha bravo charlie",
                author_id=1,
                conversation_id=1,
            )
            for i in range(1, 201)
        ]
        parsed = ParsedQuery(
            raw_query='"alpha" "bravo" "charlie"',
            search_terms=[],
            exact_phrases=spies,
        )

        filtered = apply_filters(results, parsed, user_id=1)

        # Results are unchanged by the optimisation.
        assert [r.id for r in filtered] == [r.id for r in results]
        # The invariant case-fold happens once per phrase (3), not once per
        # (phrase, result) pair (3 * 200 = 600).
        assert lower_calls[0] == len(phrases), (
            f"phrase.lower() called {lower_calls[0]} times; expected "
            f"{len(phrases)} (once per phrase), not once per (phrase, result)"
        )

    def test_phrase_lower_results_identical_mixed_case(self):
        phrases = ["HeLLo", "WoRLD", "FoO"]
        results = [
            MessageSearchResult(
                id=i,
                message_id=i,
                content=c,
                author_id=1,
                conversation_id=1,
            )
            for i, c in enumerate(
                [
                    "hello world foo",
                    "HELLO world",
                    "nothing here",
                    "foo WORLD hello",
                    "hello WORLD FOO",
                ],
                start=1,
            )
        ]
        parsed = ParsedQuery(
            raw_query=" ".join(f'"{p}"' for p in phrases),
            search_terms=[],
            exact_phrases=phrases,
        )

        filtered = apply_filters(results, parsed, user_id=1)

        expected = [
            r for r in results if all(p.lower() in r.content.lower() for p in phrases)
        ]
        assert [r.id for r in filtered] == [r.id for r in expected]
