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


# PostgreSQL dollar-quoted strings ($$...$$ or $tag$...$tag$) are opaque
# literals just like single-quoted ones: their contents may hold any keyword
# or comment marker without the parser treating it as SQL.
_DOLLAR_QUOTE_OPEN_RE = re.compile(r"\$[A-Za-z_][A-Za-z_0-9]*\$|\$\$")


def _normalize_sql(statement: str) -> str:
    """Strip comments and blank quoted literals before denylist matching.

    PostgreSQL nests block comments: in ``/* a /* b */ c */`` the first ``*/``
    closes only the inner comment, and the text after it is still a comment to
    the database. A non-greedy ``/\\*.*?\\*/`` substitution stops at that first
    ``*/`` and leaves live-looking SQL behind (e.g. ``DELETE /* a /* b */ FROM
    t */ FROM t`` normalizes to ``DELETE FROM t */ FROM t``), which both hides
    keywords and can expose a fake ``WHERE`` that is still inside the real
    comment. Track block-comment depth instead and emit a single separator
    only when the outermost comment closes.

    Quoted regions are opaque to the SQL parser too. A keyword inside a string
    literal is data, not syntax: ``DELETE FROM t RETURNING 'WHERE'`` and
    ``DELETE FROM t USING (SELECT 'WHERE') x`` are whole-table wipes whose only
    ``WHERE`` is a literal, and a naive substring check accepts them. Likewise
    a ``/*`` inside a literal must not open a comment that swallows the rest of
    a stacked statement (``SELECT '/*'; DELETE FROM t``). Single-quoted and
    dollar-quoted literal contents are therefore blanked, and quoted identifier
    text is replaced with a harmless placeholder so a table literally named
    ``"WHERE"`` cannot manufacture a scope restriction either. Comments are
    recognized only outside these quoted regions.

    That single space preserves keyword boundaries (so ``DELETE/**/FROM`` is
    still recognized as ``DELETE FROM``) and makes trailing comments
    irrelevant: a ``WHERE`` that only exists inside a comment or a literal is
    not a scope restriction and must not disable the guard. An unterminated
    comment or literal swallows everything after it, as it does for the
    database.
    """
    out: list[str] = []
    depth = 0
    in_line_comment = False
    index = 0
    length = len(statement)

    while index < length:
        char = statement[index]
        following = statement[index + 1] if index + 1 < length else ""

        if in_line_comment:
            if char in "\r\n":
                # The comment ends at the newline; the newline is a separator.
                out.append(" ")
                in_line_comment = False
            index += 1
            continue

        if depth:
            # Inside a block comment only /* and */ are significant. PostgreSQL
            # nests them: -- here is ordinary comment text, not a line comment.
            if char == "/" and following == "*":
                depth += 1
                index += 2
                continue
            if char == "*" and following == "/":
                depth -= 1
                index += 2
                if depth == 0:
                    out.append(" ")
                continue
            index += 1
            continue

        if char == "'":
            # SQL string literal; a doubled '' is an escaped quote inside it.
            # Blanking the contents keeps the WHERE/DELETE tokens outside the
            # literal intact while making their in-literal copies invisible.
            index += 1
            while index < length:
                if statement[index] == "'":
                    if index + 1 < length and statement[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            out.append(" ")
            continue

        if char == "$":
            dollar = _DOLLAR_QUOTE_OPEN_RE.match(statement, index)
            if dollar is not None:
                delimiter = dollar.group(0)
                closing = statement.find(delimiter, dollar.end())
                index = length if closing < 0 else closing + len(delimiter)
                out.append(" ")
                continue

        if char == '"':
            # Quoted identifier: keep a harmless token so a table name still
            # triggers unscoped-DELETE detection, but drop the original text
            # so a table named "WHERE" cannot fake a scope restriction.
            index += 1
            while index < length:
                if statement[index] == '"':
                    if index + 1 < length and statement[index + 1] == '"':
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            out.append('"x"')
            continue

        if char == "/" and following == "*":
            depth = 1
            index += 2
            continue
        if char == "-" and following == "-":
            in_line_comment = True
            index += 2
            continue

        out.append(char)
        index += 1

    return " ".join("".join(out).split())


# Statements that would destroy shared data if executed unscoped. Cleanup
# code paths are checked against this denylist in tests to keep the
# self-test permanently non-destructive.
#
# A DELETE is only safe when it carries a WHERE scope restriction. Matching
# the statement's *end* is not enough: ``DELETE FROM t RETURNING *``,
# ``DELETE FROM t ORDER BY id LIMIT 10`` and ``DELETE FROM t USING other``
# are whole-table wipes that no longer end at the table name, so an
# end-anchored ``DELETE FROM t$`` denylist silently accepts them. Key the
# check on the presence of a real WHERE clause instead; a trailing comment
# that once held a WHERE has already been removed by ``_normalize_sql``.
_UNSCOPED_DELETE_RE = re.compile(r"\bDELETE\s+FROM\s+[\"'`]?\w+[\"'`]?", re.IGNORECASE)
_SCOPE_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)

_FORBIDDEN_PATTERNS = (
    re.compile(r"DROP\s+TABLE", re.IGNORECASE),
    re.compile(r"TRUNCATE", re.IGNORECASE),
)


def assert_non_destructive(statement: str) -> None:
    """Raise if a statement is an unscoped whole-table wipe.

    Used by selftest cleanup code (and its tests) to guarantee the suite
    can never destroy shared data on a live deployment. Trailing semicolons
    and SQL comments are ignored so they cannot be used to disguise an
    otherwise unscoped DELETE; comments used *between* keywords are also
    normalized away so a comment can never act as a keyword separator. A
    DELETE is accepted only with a genuine WHERE clause -- trailing clauses
    such as RETURNING / USING / ORDER BY / LIMIT do not scope the delete.
    """
    normalized = _normalize_sql(statement)
    if _UNSCOPED_DELETE_RE.search(normalized) and not _SCOPE_WHERE_RE.search(
        normalized
    ):
        raise ValueError(
            f"Self-test cleanup refuses destructive statement: {normalized[:120]}"
        )
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
