"""Process-local cache of assembled SQL text keyed by query *shape*.

Every query builder re-assembles identical SQL text on each identical query:
WHERE fragments are rebuilt with f-strings, ``SET``/``VALUES`` column lists are
re-derived from dict key order, and LIMIT/OFFSET suffixes are re-concatenated.
That work is pure -- it depends only on the query's *shape* (validated target
table, ordered column names, validated operators, IN-list lengths and
LIMIT/OFFSET values), never on the bound values.

This module stores the compiled statement text for a shape so repeated queries
skip assembly entirely.  Bound parameters are deliberately **not** cached:
callers rebuild them from the live values on every call, so identical shapes
with different values reuse one SQL string while keeping values parameterized.

Safety is unchanged by the cache:

* shape keys are formed only from inputs that have already passed the ordinary
  identifier/operator/schema validation on that very build, so an invalid query
  is rejected before it can ever reach a cache lookup;
* the compile factory is the same sanitized assembly the builders have always
  used (all text comes from :mod:`sql_safety` helpers); and
* failed compilations are never stored (``get_or_prepare`` caches only a
  successfully returned value).

The backing store is the bounded, thread-safe ``PreparedStatementCache`` used
by ``Database.execute()``, so a hostile stream of distinct shapes cannot turn
this into an unbounded map.
"""

from typing import Any, Callable, Dict

from ..core.statements import PreparedStatementCache

#: Comfortably larger than the hot query surface, bounded by the 4096 hard cap
#: enforced by ``PreparedStatementCache``.
DEFAULT_SHAPE_CACHE_SIZE = 512

_SQL_TEXT_CACHE = PreparedStatementCache(DEFAULT_SHAPE_CACHE_SIZE)


def compiled_sql(shape: Any, factory: Callable[[], str]) -> str:
    """Return SQL text for ``shape``, assembling it on a miss.

    ``shape`` must be a hashable tuple built from already-validated structural
    inputs.  ``factory`` must be a pure function of those inputs (no bound
    values) so a cache hit is byte-for-byte identical to a fresh assembly.
    """
    return _SQL_TEXT_CACHE.get_or_prepare(shape, factory)


def reset_statement_text_cache() -> None:
    """Drop all cached statement text and reset counters (test isolation)."""
    _SQL_TEXT_CACHE.clear()
    _SQL_TEXT_CACHE.reset_stats()


def statement_text_cache_stats() -> Dict[str, int]:
    """Snapshot of hit/miss/eviction counters (used by tests and diagnostics)."""
    return _SQL_TEXT_CACHE.stats()
