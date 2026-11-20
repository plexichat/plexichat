"""Bounded, flush-guaranteed buffer for audit-log INSERT statements.

Audit writes are frequently emitted from tight loops (bulk role assignment,
temporary-membership purge, ...).  Writing one row per event costs one INSERT
plus one autocommit per event, which quickly dominates those operations.

:class:`AuditWriteBuffer` collects the parameter tuples for a *single* INSERT
statement and flushes them with one ``execute_many`` call.  Two properties keep
audit integrity intact:

* **Bounded** -- once ``capacity`` rows have accumulated the buffer flushes
  automatically, so memory use is ``O(capacity)``, never ``O(events)``.
* **Flush-guaranteed** -- :meth:`close` (called on context-manager exit) always
  flushes, including when the body raised.  No accepted row is silently
  dropped.  A failed flush is never swallowed when nothing else is wrong; if
  another exception is already propagating it is logged instead, so the
  original error is not masked.

Rows are removed from the buffer only *after* a successful flush, so a failed
flush leaves them pending instead of losing them.  A buffer instance is owned
by a single thread (callers keep it in thread-local storage).

Durability tradeoff: batching shortens the number of commit points from one
per event to one per batch.  On normal return and on handled exceptions every
row is flushed before the operation completes; a hard process crash mid-loop
can lose the rows of the current (unflushed) batch.  This is the standard
audit-batching tradeoff and is why the buffer is only used around short bulk
loops that always exit through the flush-guaranteed context manager.
"""

import logging
import sys
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Type

logger = logging.getLogger(__name__)

Params = Tuple[Any, ...]


class AuditWriteBuffer:
    """Collect audit INSERT parameter tuples and flush them as one batch."""

    def __init__(self, db: Any, sql: str, capacity: int = 256) -> None:
        capacity = int(capacity)
        if capacity < 1:
            raise ValueError("audit buffer capacity must be >= 1")
        self.db = db
        self.sql = sql
        self.capacity = capacity
        self._pending: List[Params] = []

        # Counters make the before/after effect observable without a profiler.
        self.accepted_count = 0
        self.flushed_count = 0
        self.flush_call_count = 0

    def add(self, params: Sequence[Any]) -> None:
        """Accept one audit row, auto-flushing when the buffer fills up."""
        self._pending.append(tuple(params))
        self.accepted_count += 1
        if len(self._pending) >= self.capacity:
            self.flush()

    def add_many(self, rows: Iterable[Sequence[Any]]) -> None:
        """Accept several audit rows (still auto-flushing at capacity)."""
        for row in rows:
            self.add(row)

    def flush(self) -> int:
        """Write every pending row with one ``execute_many`` call.

        Returns the number of rows written.  On failure the rows stay pending
        so the caller can retry; the exception is re-raised unchanged.
        """
        if not self._pending:
            return 0
        batch = list(self._pending)
        self.db.execute_many(self.sql, batch)
        # Only clear what was actually written.  Nothing can be added during
        # the synchronous execute_many call, but slicing by count keeps this
        # correct even if that changes.
        del self._pending[: len(batch)]
        self.flushed_count += len(batch)
        self.flush_call_count += 1
        return len(batch)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def __len__(self) -> int:
        return len(self._pending)

    def close(self) -> None:
        """Final flush, safe to call during exception unwinding.

        Flush failures surface to the caller when the buffer is closing
        normally.  While another exception is propagating they are logged
        instead of raised, so the original error is preserved (audit rows
        remain pending and the failure is visible in the logs).
        """
        if sys.exc_info()[0] is not None:
            try:
                self.flush()
            except Exception:
                logger.exception(
                    "Failed to flush bounded audit buffer while another "
                    "exception was propagating"
                )
        else:
            self.flush()

    def __enter__(self) -> "AuditWriteBuffer":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Any,
    ) -> bool:
        self.close()
        return False  # never suppress the caller's exception
