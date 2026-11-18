"""Self-test safety guard and error sanitization utilities.

The self-test is a routine, non-destructive status check. It may run
against a live deployment at any time and must never perform destructive
operations (whole-table wipes, migration rollbacks, deletion of data not
owned by ``selftest_%`` accounts). Cleanup functions in the selftest
package must therefore scope every DELETE/UPDATE to selftest-owned rows;
there is deliberately no "disposable database" escape hatch.
"""

import re
from typing import Any


# SQL comments are whitespace to the database: ``DELETE/**/FROM/**/t`` and
# ``DELETE -- note\nFROM t`` are valid whole-table wipes. Matching the raw
# (or merely whitespace-collapsed) statement therefore lets a destructive
# statement slip past the denylist simply by hiding its keywords behind a
# comment used as a separator. Strip comments first, then match.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_UNTERMINATED_BLOCK_COMMENT_RE = re.compile(r"/\*.*\Z", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\r\n]*")


def _normalize_sql(statement: str) -> str:
    """Strip SQL comments and collapse whitespace before denylist matching.

    Replacing each comment with a single space preserves keyword boundaries
    (so ``DELETE/**/FROM`` is still recognized as ``DELETE FROM``) and makes
    trailing comments irrelevant: a ``WHERE`` that only exists inside a
    comment is not a scope restriction and must not disable the guard.
    """
    text = _BLOCK_COMMENT_RE.sub(" ", statement)
    text = _UNTERMINATED_BLOCK_COMMENT_RE.sub(" ", text)
    text = _LINE_COMMENT_RE.sub(" ", text)
    return " ".join(text.split())


# Statements that would destroy shared data if executed unscoped. Cleanup
# code paths are checked against this denylist in tests to keep the
# self-test permanently non-destructive.
#
# The unscoped-DELETE pattern must run to the *logical* end of the statement.
# A bare ``DELETE FROM t`` is the obvious case, but ``DELETE FROM t;`` or
# ``DELETE FROM t; SELECT 1`` is just as destructive. Comments have already
# been removed by ``_normalize_sql``; allow a statement terminator (and any
# following statements) after the table name -- but not a WHERE clause.
_FORBIDDEN_PATTERNS = (
    re.compile(
        r"DELETE\s+FROM\s+[\"'`]?\w+[\"'`]?\s*(?:;.*)?$",
        re.IGNORECASE,
    ),  # unscoped whole-table wipe (with optional trailing statements)
    re.compile(r"DROP\s+TABLE", re.IGNORECASE),
    re.compile(r"TRUNCATE", re.IGNORECASE),
)


def assert_non_destructive(statement: str) -> None:
    """Raise if a statement is an unscoped whole-table wipe.

    Used by selftest cleanup code (and its tests) to guarantee the suite
    can never destroy shared data on a live deployment. Trailing semicolons
    and SQL comments are ignored so they cannot be used to disguise an
    otherwise unscoped DELETE; comments used *between* keywords are also
    normalized away so a comment can never act as a keyword separator.
    """
    normalized = _normalize_sql(statement)
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(normalized):
            raise ValueError(
                f"Self-test cleanup refuses destructive statement: {normalized[:120]}"
            )


def sanitize_error(value: Any, limit: int = 200) -> str:
    """Sanitize raw exception/error strings before storing shared state.

    Error details end up in shared result state that can be surfaced by
    status endpoints, so they are reduced to a single-line, truncated
    summary (exception type + message) with control characters removed.
    """
    if isinstance(value, BaseException):
        text = f"{type(value).__name__}: {value}"
    else:
        try:
            text = str(value)
        except Exception:
            text = repr(value)
    text = " ".join(text.split())
    return text[:limit]
