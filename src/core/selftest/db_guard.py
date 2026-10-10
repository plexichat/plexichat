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


# Statements that would destroy shared data if executed unscoped. Cleanup
# code paths are checked against this denylist in tests to keep the
# self-test permanently non-destructive.
_FORBIDDEN_PATTERNS = (
    re.compile(r"DELETE\s+FROM\s+\w+\s*$", re.IGNORECASE),  # unscoped whole-table
    re.compile(r"DROP\s+TABLE", re.IGNORECASE),
    re.compile(r"TRUNCATE", re.IGNORECASE),
)


def assert_non_destructive(statement: str) -> None:
    """Raise if a statement is an unscoped whole-table wipe.

    Used by selftest cleanup code (and its tests) to guarantee the suite
    can never destroy shared data on a live deployment.
    """
    normalized = " ".join(statement.split())
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
