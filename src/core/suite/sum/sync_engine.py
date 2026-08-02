"""Protocols and invariants for the future PlexiSum data layer.

The server implementation may use SQLite for a single-node deployment and
PostgreSQL for larger deployments. The domain contract must remain independent
of either engine. SQLite/WASM is also a candidate for a local browser cache.
"""

from typing import Iterable, Protocol

from .schema import (
    SumOperationEnvelope,
    SumViewportQuery,
    SumViewportResponse,
)


class SumDataStore(Protocol):
    """Repository boundary for bounded workbook reads and durable operations."""

    def read_viewport(self, query: SumViewportQuery) -> SumViewportResponse:
        """Return only the requested bounded range plus current revision."""
        ...

    def append_operation(self, operation: SumOperationEnvelope) -> int:
        """Validate and append an operation, returning its server revision."""
        ...

    def replay_operations(
        self, workbook_id: str, after_revision: int
    ) -> Iterable[SumOperationEnvelope]:
        """Return operations after a client's acknowledged revision."""
        ...

    def compact_snapshot(self, workbook_id: str) -> None:
        """Compact old operations into a durable snapshot when safe."""
        ...


# Storage design rules:
#
# * Use stable physical tables and a logical dynamic schema; never interpolate
#   user column names into DDL or SQL.
# * Prefer typed sparse values for irregular sheets and column segments for
#   dense, large columns; choose through measured workload, not assumptions.
# * Index row/column identity and explicitly selected filter/sort workloads.
# * Bound every viewport, paste, operation, snapshot, and replay response.
# * Operation IDs are idempotency keys. Replays must not duplicate mutations.
# * A base revision mismatch is handled explicitly; it is not silently lost.
# * The UI engine is replaceable and never authoritative for persistence.
# * Do not claim "faster than Excel" without reproducible workload benchmarks.
