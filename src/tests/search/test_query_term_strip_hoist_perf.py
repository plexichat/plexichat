"""Pin the search-term normalization hot path to one ``strip`` per token.

``QueryParser.parse`` builds its ``search_terms`` list with a comprehension::

    [term.strip() for term in remaining.split()
     if term.strip() and not term.startswith("-:")]

That calls the pure, immutable ``str.strip()`` normalizer **twice** on the
same ``term``: once as the predicate and once as the emitted value. ``term``
is a plain string produced by ``split()`` and cannot change between the two
calls, so one computation can be hoisted and reused with identical results.

This is a genuine hot path — it runs on every advanced-search request and
scales with the number of whitespace-separated tokens in the query. Every
extra ``strip`` per token is avoidable work on that path.

The test below counts ``str.strip`` calls with ``sys.setprofile`` (a Python
method on an immutable builtin cannot be monkeypatched). It compares queries
differing by exactly one token, so the per-token cost is isolated from the
constant ``query.strip()`` overhead at the top of ``parse``. Before the fix
the delta is 2 strips per token; after hoisting it is 1.
"""

import sys

import pytest

from src.core.search.query.parser import QueryParser

pytestmark = [pytest.mark.unit, pytest.mark.search]


def _strip_call_count(query: str) -> int:
    """Return the number of ``str.strip`` calls made while parsing ``query``."""
    parser = QueryParser()
    calls = 0

    def profiler(frame, event, arg):
        nonlocal calls
        if event == "c_call" and getattr(arg, "__name__", None) == "strip":
            calls += 1
        return profiler

    sys.setprofile(profiler)
    try:
        parser.parse(query)
    finally:
        sys.setprofile(None)
    return calls


def test_term_normalization_strips_once_per_token():
    """Adding one plain token must add exactly one strip, not two."""
    fewer = "alpha beta gamma"
    more = "alpha beta gamma delta"

    parsed = QueryParser().parse(more)
    assert parsed.search_terms == ["alpha", "beta", "gamma", "delta"]

    before = _strip_call_count(fewer)
    after = _strip_call_count(more)
    assert after - before == 1, (
        f"each additional search token should cost one strip, "
        f"got {after - before} ({before} strips for 3 tokens, "
        f"{after} strips for 4 tokens)"
    )
