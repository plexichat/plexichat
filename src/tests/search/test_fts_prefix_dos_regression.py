"""Regression: FTS5 prefix queries must not be built from 1-2 character terms.

SQLite FTS5 implements a trailing ``*`` prefix query by scanning the whole term
dictionary and OR-ing every posting list whose term starts with that prefix.
The cost is independent of the result LIMIT. Before this fix every bare search
term became a prefix query, so a query such as ``a b c ... p`` (16 one-letter
terms, within the existing term cap) forced a full-index prefix merge on every
request -- a cheap authenticated DoS. Terms shorter than
``_FTS_MIN_PREFIX_TERM_CHARS`` are now matched exactly while 3+ character
prefixes keep the prefix/search-as-you-type behaviour.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core.search.indexer.sqlite_fts import (
    SQLiteFTS5Indexer,
    _FTS_MAX_TERMS,
    _FTS_MIN_PREFIX_TERM_CHARS,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _fts(query: str) -> str:
    # _build_fts_query is pure; no initialized indexer/DB is needed.
    return SQLiteFTS5Indexer._build_fts_query(object.__new__(SQLiteFTS5Indexer), query)


def test_short_bare_terms_are_exact_not_prefixes():
    # 1- and 2-character terms must not carry the expensive trailing '*'.
    assert _fts("a") == '"a"'
    assert _fts("ab cd") == '"ab" OR "cd"'


def test_longer_bare_terms_keep_prefix_matching():
    # Ordinary, meaningfully long prefixes still get prefix semantics.
    assert _fts("hello") == '"hello"*'
    assert _fts("hel wor") == '"hel"* OR "wor"*'
    assert _FTS_MIN_PREFIX_TERM_CHARS == 3


def test_quoted_phrases_remain_exact():
    # Phrases never had a '*'; make sure the fix did not change that.
    assert _fts('"hello world"') == '"hello world"'


def test_term_cap_is_still_enforced():
    # 20 terms -> capped at _FTS_MAX_TERMS tokens.
    tokens = _fts(" ".join(chr(97 + i) for i in range(20))).split(" OR ")
    assert len(tokens) == _FTS_MAX_TERMS
    # And none of the capped short terms turned into a prefix query.
    assert all(not token.endswith("*") for token in tokens)


def test_generated_short_query_matches_only_exact_term():
    # Behavioural check: run the generated MATCH expression against a real
    # FTS5 index. An exact 1-char query must not match longer words sharing
    # that prefix (which the old '"a"*' form would have matched).
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE f USING fts5(content)")
    db.executemany(
        "INSERT INTO f(content) VALUES (?)",
        [("alpha",), ("a",), ("atom",), ("beta",)],
    )
    db.commit()

    exact = _fts("a")
    rows = db.execute(
        "SELECT rowid FROM f WHERE f MATCH ? ORDER BY rank", (exact,)
    ).fetchall()
    assert len(rows) == 1, "exact 1-char term must match only the exact token"

    # The generated expression is valid FTS5 syntax for all shapes.
    for query in ("a", "ab cd", "hello world", "a b c d e f g h i j k l m n o p"):
        db.execute("SELECT rowid FROM f WHERE f MATCH ?", (_fts(query),)).fetchall()
