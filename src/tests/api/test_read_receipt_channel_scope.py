"""Regression: MESSAGE_ACK read receipts are scoped to channel viewers.

A read receipt must never be broadcast to every member of a server — doing so
leaks message activity in restricted/private channels. The route must use the
scoped resolver, not a raw ``get_member_user_ids`` lookup.
"""

import ast
from pathlib import Path

_ROUTE = (
    Path(__file__).resolve().parents[2]
    / "api"
    / "routes"
    / "messages"
    / "read_receipts.py"
)


def _source() -> str:
    return _ROUTE.read_text(encoding="utf-8")


def test_route_uses_scoped_recipient_resolver():
    source = _source()
    assert "resolve_channel_event_recipients" in source, (
        "read_receipts must scope ACK recipients through "
        "resolve_channel_event_recipients"
    )


def test_route_does_not_use_raw_member_list_for_ack():
    source = _source()
    tree = ast.parse(source)
    # No call site to get_member_user_ids may feed a MESSAGE_ACK dispatch.
    # The route must rely on the scoped resolver instead.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get_member_user_ids":
                raise AssertionError(
                    "raw get_member_user_ids must not be used for ACK scoping"
                )


def test_scoped_resolver_fails_closed():
    from src.core.events.gateway_emit import resolve_channel_event_recipients

    # A missing/unresolvable channel yields no recipients (fail closed).
    assert resolve_channel_event_recipients(None) == []
