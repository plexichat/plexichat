"""Regression tests for the directly-callable SQLite FTS5 query boundary.

The HTTP search entry points reject control characters, but indexers are also
invoked directly (rebuilds, tests, internal callers). ``_build_fts_query`` used
to forward a NUL byte into the bound FTS5 MATCH expression, where sqlite3
raises ``OperationalError: unterminated string`` -- a single "%00" turned a
search into a 500. Quoted phrases were also exempt from ``_FTS_MAX_TERMS``, so
a short needle made entirely of ``"a" "b" ...`` could force FTS5 to plan dozens
of prefix phrases and defeat the term cap.
"""

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.search.indexer.sqlite_fts import SQLiteFTS5Indexer, _FTS_MAX_TERMS

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.search]


class _FakeDB:
    """Minimal DB double that records emitted SELECT statements."""

    type = "sqlite"

    def __init__(self):
        self.selects = []

    def execute(self, query, params=None):
        return None

    def fetch_one(self, query, params=None):
        return None

    def fetch_all(self, query, params=None):
        self.selects.append((query, tuple(params or ())))
        return []


def _indexer():
    indexer = SQLiteFTS5Indexer(_FakeDB())
    # Query construction is under test; skip the schema probe bootstrap.
    indexer._initialized = True
    return indexer


@pytest.mark.parametrize(
    "needle",
    [
        "he\x00llo",
        '"he\x00llo"',
        "a\x00 b",
        "q\x1f",
        "x\x7f",
        "line\u2028sep",
    ],
)
@pytest.mark.parametrize(
    "method", ["search_messages", "search_users", "search_servers"]
)
def test_control_chars_never_reach_match(needle, method):
    """All public search methods fail closed before building a MATCH string."""
    indexer = _indexer()
    with pytest.raises(HardeningViolation):
        getattr(indexer, method)(needle)
    # No SQL was emitted at all: the payload was rejected at the boundary.
    assert indexer._db.selects == []


@pytest.mark.parametrize("needle", ['"he\x00llo"', "he\x00llo", "x\x1f"])
def test_build_fts_query_rejects_control_chars(needle):
    indexer = SQLiteFTS5Indexer(None)
    with pytest.raises(HardeningViolation):
        indexer._build_fts_query(needle)


def test_quoted_phrases_share_the_term_budget():
    """A needle made only of quoted phrases cannot exceed the term cap."""
    indexer = SQLiteFTS5Indexer(None)
    needle = " ".join(f'"t{i}"' for i in range(60))
    fts_query = indexer._build_fts_query(needle)
    # Every token is a quoted phrase, so one double quote pair == one term.
    assert fts_query.count('"') // 2 <= _FTS_MAX_TERMS


def test_mixed_phrases_and_terms_share_one_budget():
    indexer = SQLiteFTS5Indexer(None)
    needle = " ".join([f'"p{i}"' for i in range(_FTS_MAX_TERMS)] + ["extra", "terms"])
    fts_query = indexer._build_fts_query(needle)
    assert fts_query.count('"') // 2 <= _FTS_MAX_TERMS


def test_normal_mixed_query_unaffected():
    """Ordinary quoted + bare queries keep their original shape."""
    indexer = SQLiteFTS5Indexer(None)
    assert indexer._build_fts_query('"hello world" foo') == '"hello world" OR "foo"*'
