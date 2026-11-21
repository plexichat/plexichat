"""Regression: PostgreSQL's ``#`` XOR operator must not mask a stacked write
from the query-cache write classifier.

On PostgreSQL ``#`` is the integer XOR operator, NOT a comment — both supported
dialects use only ``--`` and ``/* ... */``. Parameterless SQL is sent through
pg8000's simple-query protocol, which executes every ``;``-separated statement
in the string. ``_SQL_NON_CODE_RE`` used to neutralise ``#[^\n]*`` as a comment,
so a stacked ``; UPDATE ...`` behind an XOR expression (e.g. ``SELECT 1 # 1;
UPDATE authz_roles SET role = 'user' ...``) was hidden from
``_has_multiple_statements()``: the whole string looked like a single read, the
query cache was not cleared, and a cached admin verdict survived the demotion.

Reachability: ``_sanitize_sqlite_query()`` (which strips ``#`` tails) is applied
only for ``self.type == "sqlite"``; PostgreSQL receives the raw SQL, and
``dialect.convert_placeholders()`` does not touch ``#``. ``_query_mutates_data``
runs on that raw text in ``execute()``/``execute_many()``. SQLite's fake driver
cannot model PG's XOR, so this test asserts the classifier directly; the
preparation path above establishes that the raw ``#`` string reaches PG.
"""

import pytest
from src.core.database.core.execution import (
    _has_multiple_statements,
    _query_mutates_data,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

# The exact shape from the audit: a PG XOR expression followed by a stacked DML
# that demotes an admin role. On PG both statements execute; the cache must be
# cleared so a cached "admin" verdict cannot survive the write.
PG_XOR_STACKED_WRITE = (
    "SELECT 1 # 1; UPDATE authz_roles SET role = 'user' WHERE user_id = 1"
)


def test_hash_xor_stacked_write_is_classified_as_write():
    assert _query_mutates_data(PG_XOR_STACKED_WRITE) is True


def test_hash_xor_stacked_write_is_detected_as_multiple_statements():
    assert _has_multiple_statements(PG_XOR_STACKED_WRITE) is True


def test_non_vacuous_single_xor_expression_is_still_a_read():
    # A lone XOR with no separator is a genuine read on PG (and, more
    # importantly, does not carry a hidden statement). It must stay a read so
    # the assertions above are not passing merely because any '#' is a write.
    assert _query_mutates_data("SELECT 5 # 3") is False
    assert (
        _query_mutates_data("SELECT role FROM authz_roles WHERE user_id = ?") is False
    )


def test_non_vacuous_plain_stacked_write_is_still_a_write():
    # Sanity: the pre-existing stacked-write detection remains intact.
    assert (
        _query_mutates_data("SELECT 1; UPDATE authz_roles SET role = 'user'") is True
    )


def test_hash_inside_string_literal_is_not_a_separator():
    # Removing '#' from the non-code pattern must not disturb literal masking.
    assert _query_mutates_data("SELECT '#a;b'") is False
