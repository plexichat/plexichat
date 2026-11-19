"""SEC400 invariant: hardened route modules parse boundary IDs with ``parse_id``.

A bare ``int(request.query_params["channel_id"])`` accepts signs, underscores,
Unicode digits, whitespace and values past the 64-bit snowflake ceiling. The
hardening fix replaced that with the shared, ASCII-only and range-bounded
``src.core.security.hardening.parse_id`` helper at the route boundary. Several
subsystems adopted that fix (messaging, channel attachments, polls); this suite
ratchets those adopters so a future refactor cannot silently reintroduce a bare
``int()`` on an incoming ID/cursor parameter.

The check is purely AST-based over source files -- no app import, config or DB
is needed, so it cannot flake. The detector is first exercised against
synthetic good/bad handlers (self-test), then against the real tree, and a
non-vacuity test proves the scan really sees both the helper calls and the
route handlers it is meant to guard.
"""

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# Route modules that adopted the shared parse_id boundary helper as part of the
# prior fix. Every one must still reference parse_id and must never convert one
# of its own string-typed route ID/cursor parameters with a bare int().
HARDENED_ID_MODULES = (
    "src/api/routes/messages/helpers.py",
    "src/api/routes/messages/messages_list.py",
    "src/api/routes/messages/read_receipts.py",
    "src/api/routes/messages/typing.py",
    "src/api/routes/messages/search.py",
    "src/api/routes/messages/export.py",
    "src/api/routes/messages/pins.py",
    "src/api/routes/messages/messages_crud/send.py",
    "src/api/routes/messages/messages_crud/delete.py",
    "src/api/routes/messages/messages_crud/retrieve.py",
    "src/api/routes/messages/messages_crud/edit.py",
    "src/api/routes/channels/helpers.py",
    "src/api/routes/channels/attachments.py",
    "src/api/routes/polls.py",
)

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _is_id_or_cursor(name: str) -> bool:
    return (
        name == "id"
        or name.endswith("_id")
        or name
        in {
            "cursor",
            "after",
            "before",
        }
    )


def _annotation_is_str(annotation) -> bool:
    """Recognise ``str`` and ``Optional[str]``-shaped boundary annotations."""
    if annotation is None:
        return False
    if isinstance(annotation, ast.Name):
        return annotation.id == "str"
    if isinstance(annotation, ast.Constant):
        return annotation.value == "str"
    if isinstance(annotation, ast.Subscript):
        return _annotation_is_str(annotation.value) or (
            isinstance(annotation.slice, ast.Name) and annotation.slice.id == "str"
        )
    return False


def _is_route_handler(node) -> bool:
    return any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in HTTP_METHODS
        for dec in node.decorator_list
    )


def _boundary_params(handler):
    names = set()
    args = handler.args
    for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        if _annotation_is_str(arg.annotation) and _is_id_or_cursor(arg.arg):
            names.add(arg.arg)
    return names


def _bare_int_on_param_violations(tree):
    """Return ``(lineno, handler, param)`` for bare int() on a boundary param."""
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_route_handler(node):
            continue
        params = _boundary_params(node)
        if not params:
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "int"
                and sub.args
                and isinstance(sub.args[0], ast.Name)
                and sub.args[0].id in params
            ):
                violations.append((sub.lineno, node.name, sub.args[0].id))
    return violations


def _parse_id_calls(tree) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "parse_id")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "parse_id")
        )
    )


def _route_handler_count(tree) -> int:
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _is_route_handler(node)
    )


def _scan_tree():
    scanned = []
    for relative in HARDENED_ID_MODULES:
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scanned.append(
            (
                relative,
                _bare_int_on_param_violations(tree),
                _parse_id_calls(tree),
                _route_handler_count(tree),
            )
        )
    return scanned


_SCAN = _scan_tree()


def test_detector_catches_bare_int_but_accepts_parse_id_and_non_str_params():
    """Self-test: the detector must flag the regression shape and only that."""
    bad = """
@router.get("/bad")
def bad(channel_id: str, cursor: str):
    cid = int(channel_id)
    cur = int(cursor)
    return cid, cur
"""
    good = """
@router.get("/good")
def good(channel_id: str):
    return parse_id(channel_id, "channel_id")
"""
    # Non-string / non-ID parameters are out of scope: coercing an int-typed
    # quantity or a free-text field with int() is not a boundary-ID regression.
    out_of_scope = """
@router.get("/other")
def other(count: int, label: str):
    return int(count), int(label)
"""
    assert len(_bare_int_on_param_violations(ast.parse(bad))) == 2

    good_tree = ast.parse(good)
    assert _bare_int_on_param_violations(good_tree) == []
    assert _parse_id_calls(good_tree) == 1

    assert _bare_int_on_param_violations(ast.parse(out_of_scope)) == []


def test_hardened_modules_still_use_parse_id_and_scan_is_not_vacuous():
    assert len(_SCAN) == len(HARDENED_ID_MODULES)
    assert all(rel.startswith("src/api/routes/") for rel, *_ in _SCAN)
    # Every adopter still calls the helper, and the scan really covers route
    # handlers -- otherwise the "no bare int()" assertion below is vacuous.
    missing_helper = [rel for rel, _, calls, _ in _SCAN if calls == 0]
    assert not missing_helper, f"parse_id dropped from: {missing_helper}"
    total_calls = sum(calls for _, _, calls, _ in _SCAN)
    total_handlers = sum(handlers for _, _, _, handlers in _SCAN)
    assert total_calls >= 15, f"only {total_calls} parse_id calls discovered"
    assert total_handlers >= 20, f"only {total_handlers} route handlers discovered"


def test_no_hardened_route_converts_a_boundary_id_with_bare_int():
    offenders = [
        f"{rel}:{lineno} in {handler}({param})"
        for rel, violations, _, _ in _SCAN
        for lineno, handler, param in violations
    ]
    assert not offenders, (
        "Bare int() on a string ID/cursor route parameter reintroduced:\n  "
        + "\n  ".join(sorted(offenders))
    )
