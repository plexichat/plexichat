"""SEC200 invariant: event/ACK recipients in message routes are channel-scoped.

Read receipts (``MESSAGE_ACK``) used to be broadcast to the raw server-member
list via ``get_member_user_ids``. That leaks message activity in restricted and
private channels, because a member of the server is not necessarily a viewer of
the channel. The fix routes server-channel ACKs through
``resolve_channel_event_recipients`` (which filters by ``channels.view`` and
fails closed) and falls back to conversation participants only for DMs/groups.

This suite is a pure source-level (AST) lock on that invariant, so it needs no
app import, no config and no database and cannot flake on fixture ordering. It
asserts three things:

* every function that dispatches a ``MESSAGE_ACK`` resolves its recipients via
  the scoped resolver, never via a raw ``get_member_user_ids`` lookup;
* the read-receipt route module contains no raw member lookup at all;
* the *only* remaining raw ``get_member_user_ids`` uses in the message route
  tree (message create/update/delete broadcasts) apply a per-member
  ``channels.view`` permission filter, so a bare lookup cannot creep back in.

A vacuous scan would trivially satisfy all of the above, so explicit
non-vacuity checks pin the expected call shapes.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MESSAGE_ROUTES = ROOT / "src/api/routes/messages"

RAW_MEMBER_LOOKUP = "get_member_user_ids"
SCOPED_RESOLVER = "resolve_channel_event_recipients"


def _module_trees():
    """Parse every message-route module into an AST, ignoring bytecode caches."""
    trees = []
    for path in sorted(MESSAGE_ROUTES.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        trees.append((path, ast.parse(path.read_text(encoding="utf-8"))))
    return trees


def _functions():
    for path, tree in _module_trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield path, node


def _call_name(node):
    """Return the bare name of a call's callee (``a.b()`` -> ``b``)."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _calls(node):
    return [child for child in ast.walk(node) if isinstance(child, ast.Call)]


def _own_body_nodes(root: ast.AST):
    """Yield ``root`` and nodes on its execution path, skipping nested defs.

    Descend through statement-level nesting (``if``/``for``/``with``/``try``/...)
    but do not descend into a nested function or lambda: *defining* a helper
    that builds a ``MESSAGE_ACK`` or resolves its recipients does not run it.
    Crediting dead code as in-handler evidence lets an unscoped dispatcher
    silence this invariant, so the positive-evidence scans use this walker.
    Violation scans (raw member lookups) stay on the broad traversal below.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _own_body_calls(node):
    return [child for child in _own_body_nodes(node) if isinstance(child, ast.Call)]


def _is_ack_dispatch(fn):
    """Whether ``fn`` itself builds a ``MESSAGE_ACK`` event.

    The event must be built on the function's own execution path: a nested def
    or lambda that mentions ``MESSAGE_ACK`` never dispatches anything.
    """
    return any(
        isinstance(node, ast.Attribute) and node.attr == "MESSAGE_ACK"
        for node in _own_body_nodes(fn)
    )


def _member_lookup_functions():
    return [
        (path, fn)
        for path, fn in _functions()
        if any(_call_name(c) == RAW_MEMBER_LOOKUP for c in _calls(fn))
    ]


def _scoping_offenses(path_name, fn):
    """Offense strings for one ACK dispatcher's recipient scoping.

    The forbidden raw lookup stays on the broad traversal (a violation detector
    must stay conservative). The scoped resolver is positive evidence, so it is
    required on the function's own execution path -- a resolver call inside a
    never-invoked nested callable does not scope any recipients.
    """
    names = {_call_name(c) for c in _calls(fn)}
    own_names = {_call_name(c) for c in _own_body_calls(fn)}
    offenses = []
    if RAW_MEMBER_LOOKUP in names:
        offenses.append(f"{path_name}:{fn.name} uses {RAW_MEMBER_LOOKUP}")
    if SCOPED_RESOLVER not in own_names:
        offenses.append(f"{path_name}:{fn.name} never calls {SCOPED_RESOLVER}")
    return offenses


# --- the scan itself must be real -------------------------------------------


def test_ack_dispatch_scan_is_not_vacuous():
    acks = [(p.name, fn.name) for p, fn in _functions() if _is_ack_dispatch(fn)]
    assert len(acks) >= 2, (
        "expected at least the single-channel and bulk ACK dispatch helpers, "
        f"found {acks}"
    )


def test_dead_nested_ack_dispatch_evidence_does_not_satisfy_guard():
    """A ``MESSAGE_ACK`` or resolver call inside a dead callable is not evidence.

    Regression: ``_is_ack_dispatch`` used ``ast.walk(fn)`` and the resolver check
    used ``_calls(fn)`` (also broad), so a function whose only ``MESSAGE_ACK``
    or ``resolve_channel_event_recipients`` call lived in an uninvoked nested
    def/lambda satisfied both the dispatch scan and the scoping guard. A live
    event behind statement-level nesting must still count.
    """
    source = """
def dead_nested():
    def never_called():
        event_type = EventType.MESSAGE_ACK
        return resolve_channel_event_recipients(ids)
    return None

def dead_lambda():
    _ = lambda: (EventType.MESSAGE_ACK, resolve_channel_event_recipients(ids))
    return None

def live():
    if True:
        event_type = EventType.MESSAGE_ACK
        return resolve_channel_event_recipients(ids)
    return None
"""
    fns = {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }
    for name in ("dead_nested", "dead_lambda"):
        assert not _is_ack_dispatch(fns[name]), f"{name} dispatch evidence is dead"
        assert _scoping_offenses("synthetic.py", fns[name]) == [
            f"synthetic.py:{name} never calls {SCOPED_RESOLVER}"
        ]
    assert _is_ack_dispatch(fns["live"]), "live in-body dispatch must still count"
    assert _scoping_offenses("synthetic.py", fns["live"]) == []

    # The old broad logic really was fooled, so this self-test is non-vacuous.
    for name in ("dead_nested", "dead_lambda"):
        fn = fns[name]
        assert any(
            isinstance(node, ast.Attribute) and node.attr == "MESSAGE_ACK"
            for node in ast.walk(fn)
        ), "old _is_ack_dispatch accepted dead nested evidence"
        assert SCOPED_RESOLVER in {_call_name(c) for c in _calls(fn)}, (
            "old resolver check accepted dead nested evidence"
        )


def test_raw_member_lookup_scan_is_not_vacuous():
    uses = [(p.name, fn.name) for p, fn in _member_lookup_functions()]
    assert uses, (
        "expected at least one permission-filtered member lookup in the "
        "message-event broadcast helper; if all call sites were removed the "
        "filter invariant below would pass vacuously"
    )


# --- the ACK/event scoping invariant ----------------------------------------


def test_ack_dispatch_uses_scoped_resolver_not_raw_member_ids():
    offenders = []
    for path, fn in _functions():
        if not _is_ack_dispatch(fn):
            continue
        offenders.extend(_scoping_offenses(path.name, fn))
    assert not offenders, (
        "MESSAGE_ACK dispatch must scope server-channel recipients through "
        f"{SCOPED_RESOLVER}, never a raw member list:\n  " + "\n  ".join(offenders)
    )


def test_read_receipt_route_has_no_raw_member_lookup():
    path = MESSAGE_ROUTES / "read_receipts.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {_call_name(c) for c in _calls(tree)}
    assert RAW_MEMBER_LOOKUP not in names, (
        "read_receipts.py must never resolve ACK recipients from the raw "
        "server-member list"
    )
    assert SCOPED_RESOLVER in names, (
        f"read_receipts.py must scope ACK recipients through {SCOPED_RESOLVER}"
    )


def test_any_remaining_raw_member_lookup_is_channel_view_filtered():
    offenders = []
    for path, fn in _member_lookup_functions():
        filtered = False
        for call in _calls(fn):
            if _call_name(call) != "has_permission":
                continue
            if any(
                isinstance(arg, ast.Constant) and arg.value == "channels.view"
                for arg in ast.walk(call)
            ):
                filtered = True
        if not filtered:
            offenders.append(f"{path.name}:{fn.name}")
    assert not offenders, (
        "any surviving get_member_user_ids call in a message route must be "
        "paired with a per-member channels.view permission filter, otherwise "
        "it can leak channel-scoped events:\n  " + "\n  ".join(offenders)
    )
