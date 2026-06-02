"""
Robust SQL statement splitter.

Existing schema files in this codebase use a simple ``schema.split(";")``
pattern to break a multi-statement DDL string into individual executable
statements. That naive split breaks for any DDL that contains a literal
semicolon inside a SQL line comment (``-- ...; ...``) or a block comment
(``/* ... ; ... */``), which has caused two prior regressions in the
``applications`` and ``avatars`` schemas.

This module provides :func:`split_sql_statements` which:

* Strips ``-- ...`` line comments before splitting (so a semicolon inside a
  comment never terminates a statement).
* Strips ``/* ... */`` block comments before splitting.
* Preserves the contents of single-quoted string literals (``'...'``)
  including any escaped quotes (``''``) so a semicolon inside a string does
  not terminate the statement.
* Preserves the contents of double-quoted identifiers (``"..."``) including
  any embedded doubled quotes (``""``) so a semicolon inside a quoted
  identifier does not terminate the statement.
* Returns slices of the *original* SQL (not the comment/string-stripped
  version) so that statements retain their full SQL syntax.

The function is intentionally permissive (it does not parse full SQL grammar
and is not a substitute for a real SQL parser); it just removes the
ambiguities that have caused production breakage in this codebase.
"""

from __future__ import annotations

import re
from typing import List


_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
_QUOTED_IDENT_RE = re.compile(r'"(?:""|[^"])*"')


def _blank_ambiguous_ranges(sql: str) -> str:
    """Replace comments and string/identifier contents with blank spaces.

    The output preserves the *length* of the original text and the position
    of every other character, but blanks out the contents of comments and
    string/identifier literals. This means a naive ``;`` split on the
    returned string will never terminate a statement inside one of these
    regions. The original slices can then be extracted from ``sql`` using
    the same character offsets.
    """
    cleaned = _BLOCK_COMMENT_RE.sub(lambda m: " " * len(m.group(0)), sql)
    cleaned = _LINE_COMMENT_RE.sub(lambda m: " " * len(m.group(0)), cleaned)
    cleaned = _STRING_LITERAL_RE.sub(lambda m: " " * len(m.group(0)), cleaned)
    cleaned = _QUOTED_IDENT_RE.sub(lambda m: " " * len(m.group(0)), cleaned)
    return cleaned


def split_sql_statements(sql: str) -> List[str]:
    """Split a multi-statement SQL string into individual statements.

    The function tolerates semicolons appearing inside line comments, block
    comments, string literals, and quoted identifiers, which the naive
    ``sql.split(";")`` cannot handle.

    The returned slices are taken from the *original* ``sql`` argument (with
    no string contents or comment text removed), preserving full SQL syntax
    including literals.
    """
    if not sql:
        return []

    cleaned = _blank_ambiguous_ranges(sql)
    statements: List[str] = []
    cursor = 0
    n = len(cleaned)
    for raw_start, raw_end in _iter_statement_ranges(cleaned):
        if raw_end <= cursor:
            continue
        # Pull the equivalent range from the original string.
        stmt = sql[cursor:raw_end].strip()
        cursor = raw_end
        if stmt:
            statements.append(stmt)
    # Trailing content after the final terminator (e.g. whitespace).
    if cursor < n:
        trailing = sql[cursor:].strip()
        if trailing:
            statements.append(trailing)
    return statements


def _iter_statement_ranges(cleaned: str):
    """Yield ``(start, end)`` character offsets for each statement in ``cleaned``.

    The offsets include the trailing semicolon (if present) and any trailing
    whitespace, so consecutive statements are extracted without overlap.
    """
    n = len(cleaned)
    start = 0
    in_statement = False
    i = 0
    while i < n:
        ch = cleaned[i]
        if ch == ";":
            # End of this statement. Yield up to and including the semicolon.
            yield (start, i + 1)
            i += 1
            # Skip whitespace/newlines until the next non-space.
            while i < n and cleaned[i] in " \t\r\n":
                i += 1
            start = i
            in_statement = False
            continue
        if ch not in " \t\r\n":
            in_statement = True
        i += 1
    if in_statement and start < n:
        yield (start, n)


__all__ = ["split_sql_statements"]
