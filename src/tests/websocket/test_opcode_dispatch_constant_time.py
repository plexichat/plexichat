"""PERF: inbound gateway opcode dispatch must be O(1), not an if/elif chain.

``OpcodeHandler.handle`` runs on EVERY inbound gateway frame. It used to walk a
~21-branch ``if op == GatewayOpcode.X`` chain, so routing a late-chain opcode
(e.g. ``SUM_OP``, the final branch) performed 21 equality comparisons per frame.
A client flooding late-chain opcodes could amplify that linear cost without
bound. The fix routes opcodes through a precomputed dict, so the comparison
count is constant regardless of where the opcode sits in the routing table.

This is a call-count regression test: it counts ``GatewayOpcode`` equality
comparisons performed while routing ONE frame and requires a constant bound.
It fails against the linear chain (21 comparisons) and passes with the table.
"""

import pytest

from src.api.websocket import handlers as handlers_module
from src.api.websocket.handlers import OpcodeHandler
from src.api.websocket.opcodes import (
    CLIENT_OPCODES,
    GatewayOpcode,
)

pytestmark = pytest.mark.websocket

# The opcodes the linear chain actually routes (client-sendable, not soft-rejected).
# Derived from the shipped throttle table so the count tracks the real chain.
_ROUTED_VALUES = frozenset(
    int(op) for op in CLIENT_OPCODES if op not in handlers_module.IGNORED_OPCODES
)


class _SpyHandlers:
    """Stand-in for every sub-handler: records which method the router picked."""

    def __getattr__(self, name: str):
        async def _fake(*args, **kwargs):
            return ("routed", name)

        return _fake


def _handler_with_spies() -> OpcodeHandler:
    handler = OpcodeHandler.__new__(OpcodeHandler)
    spies = _SpyHandlers()
    handler._connection_handler = spies
    handler._presence_handler = spies
    handler._voice_handler = spies
    handler._guild_handler = spies
    handler._artifact_handler = spies
    handler._sum_handler = spies
    handler._dispatcher = object()
    # Skip the admission path; this test isolates the routing decision itself.
    handler._allow_inbound = lambda connection, opcode: True  # type: ignore[method-assign]
    return handler


@pytest.mark.asyncio
async def test_opcode_dispatch_uses_constant_comparisons(monkeypatch):
    """Routing one late-chain opcode must not pay O(N) equality comparisons."""
    original_eq = GatewayOpcode.__eq__
    comparisons: list[tuple[int, int]] = []

    def counting_eq(self, other):
        # Only count comparisons against another routed GatewayOpcode member.
        # Plain-int comparisons from set membership and soft-reject checks are
        # not part of the dispatch chain and are deliberately excluded.
        if isinstance(other, GatewayOpcode) and int(other) in _ROUTED_VALUES:
            comparisons.append((int(self), int(other)))
        return original_eq(self, other)

    monkeypatch.setattr(GatewayOpcode, "__eq__", counting_eq)

    handler = _handler_with_spies()
    connection = type(
        "Conn",
        (),
        {
            "connection_id": "dispatch-count",
            "is_authenticated": True,
            "is_selftest": False,
            "user_id": 1,
            "_inbound_rate_buckets": {},
        },
    )()

    # SUM_OP is the last branch in the old linear chain -> worst case (21).
    result = await handler.handle(connection, int(GatewayOpcode.SUM_OP), {})
    assert result == ("routed", "handle_sum_op")

    assert len(comparisons) <= 3, (
        "opcode dispatch used "
        f"{len(comparisons)} routed equality comparisons for a single frame "
        "(expected an O(1) dict lookup, not a linear if/elif chain): "
        f"{comparisons}"
    )


def test_opcode_dispatch_table_exists_and_is_complete():
    """A module-level table must map every routed opcode, fail-closed by default."""
    table = handlers_module._OPCODE_DISPATCH_TABLE
    assert {int(op) for op in table} == set(_ROUTED_VALUES)
