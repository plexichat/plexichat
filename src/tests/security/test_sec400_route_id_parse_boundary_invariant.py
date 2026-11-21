"""SEC400 invariant: hardened route modules parse boundary IDs with ``parse_id``.

A bare ``int(request.query_params["channel_id"])`` accepts signs, underscores,
Unicode digits, whitespace and values past the 64-bit snowflake ceiling. The
hardening fix replaced that with the shared, ASCII-only and range-bounded
``src.core.security.hardening.parse_id`` helper at the route boundary. The
sweep walks EVERY shipped route module (not a curated adopter list), so a new
route under any name that reintroduces a bare ``int()`` on an incoming
ID/cursor parameter fails the guard; the original adopters are kept only as a
non-vacuity floor.

The check is purely AST-based over source files -- no app import, config or DB
is needed, so it cannot flake. The detector is first exercised against
synthetic good/bad handlers (self-test), then against the real tree, and a
non-vacuity test proves the scan really sees both the helper calls and the
route handlers it is meant to guard, including a novel-named module.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# Floor pins only: modules that adopted the shared parse_id boundary helper as
# part of the original fix. Every one must still call parse_id and stay free of
# bare int() on its string ID/cursor params. They are a floor, NOT the bound of
# the sweep: the detector below walks the whole routes package so a brand-new
# module under a name that appears in no list is still checked.
_KNOWN_PARSE_ID_ADOPTERS = frozenset(
    {
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
    }
)

_ROUTES_DIR = ROOT / "src/api/routes"

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


def _iter_route_modules(base: Path | None = None):
    """Every shipped API route module, discovered structurally.

    The old ratchet iterated a hand-curated tuple of 14 adopter paths. A new
    route module -- under any name -- that converts a string ID/cursor parameter
    with a bare ``int()`` was never examined and the guard passed silently.
    Walking the whole ``routes`` package makes the sweep name-agnostic: no
    enumeration of known filenames bounds what gets checked.
    """
    base = base if base is not None else _ROUTES_DIR
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _scan_paths(paths, root: Path | None = None):
    """Scan route-module paths, labelling them relative to ``root``.

    Shared by the real tree walk and the novel-module self-test so the latter
    exercises the exact same discovery + detector pipeline, not a mock.
    """
    root = root if root is not None else ROOT
    scanned = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scanned.append(
            (
                path.relative_to(root).as_posix(),
                _bare_int_on_param_violations(tree),
                _parse_id_calls(tree),
                _route_handler_count(tree),
            )
        )
    return scanned


def _scan_tree():
    return _scan_paths(_iter_route_modules())


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
    scanned = {rel for rel, *_ in _SCAN}
    assert all(rel.startswith("src/api/routes/") for rel in scanned)
    # The curated adopters are a floor, not the scan bound: the structural walk
    # must reach every one of them and then some, so reverting to a fixed list
    # fails non-vacuously.
    assert _KNOWN_PARSE_ID_ADOPTERS <= scanned, (
        "route sweep is missing known parse_id adopters: "
        f"{sorted(_KNOWN_PARSE_ID_ADOPTERS - scanned)}"
    )
    assert scanned - _KNOWN_PARSE_ID_ADOPTERS, (
        "route sweep regressed to the old hand-curated adopter list"
    )
    # Every known adopter still calls the helper, and the scan really covers
    # route handlers -- otherwise the "no bare int()" assertion is vacuous.
    missing_helper = [
        rel
        for rel, _, calls, _ in _SCAN
        if rel in _KNOWN_PARSE_ID_ADOPTERS and calls == 0
    ]
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


def test_novel_route_module_with_bare_int_is_flagged(tmp_path):
    """Non-vacuity: the structural sweep flags a brand-new, novel-named module.

    The old detector iterated a fixed list of 14 adopter paths, so a route
    module under any other name was invisible to the guard. This writes a
    never-before-seen module under a novel package name and proves the same
    discovery + scan pipeline the real guard uses reports its bare-int boundary
    conversion -- with no advance knowledge of the file or handler name.
    """
    routes = tmp_path / "src/api/routes/brand_new_subsystem"
    routes.mkdir(parents=True)
    module = routes / "future_widget_export.py"

    def _scan_novel(source: str):
        module.write_text(textwrap.dedent(source), encoding="utf-8")
        return _scan_paths(
            _iter_route_modules(tmp_path / "src/api/routes"), root=tmp_path
        )

    scanned = _scan_novel(
        """\
        @router.get("/future")
        def future_widget_export(channel_id: str):
            return int(channel_id)
        """
    )
    assert len(scanned) == 1
    relative, violations, _, _ = scanned[0]
    assert relative == "src/api/routes/brand_new_subsystem/future_widget_export.py"
    assert violations, "novel route module's bare int() was not detected"

    # The same novel module using the shared gate is classified as clean.
    scanned = _scan_novel(
        """\
        @router.get("/future")
        def future_widget_export(channel_id: str):
            return parse_id(channel_id, "channel_id")
        """
    )
    assert scanned[0][1] == []
