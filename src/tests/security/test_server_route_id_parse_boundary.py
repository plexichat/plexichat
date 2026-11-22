"""Server-route boundary IDs must use the ASCII/range-bounded snowflake gate.

The server route modules used to coerce their string path/query IDs with a bare
``int()``.  Python's ``int`` accepts Unicode decimal homoglyphs (``"١٢٣"``,
``"１２３"``), PEP 515 separators (``"1_2"``), signs, whitespace and values past
the 64-bit snowflake ceiling.  Every other route family (messaging, channels,
polls, suite, ...) already parses boundary IDs through
``src.core.security.hardening.parse_id``, so the same raw token was accepted by
a server route but rejected everywhere else -- a parser differential between
validation and consumption.

This suite walks EVERY shipped server route module (not a curated file list):
a refactor cannot silently bring back a bare ``int()`` on an incoming
ID/cursor parameter, even in a new module under a novel name, and the shared
gate is proven to reject the exact inputs ``int`` used to let through. The
original adopters are kept only as a non-vacuity floor.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# Floor pins only: server route modules that originally converted a string ID
# with bare int(). They are a floor, NOT the bound of the sweep: the detector
# below walks the whole ``routes/servers`` package, so a new module under a name
# that appears in no list is still checked.
_KNOWN_SERVER_ID_MODULES = frozenset(
    {
        "src/api/routes/servers/roles.py",
        "src/api/routes/servers/channels.py",
        "src/api/routes/servers/members.py",
        "src/api/routes/servers/misc.py",
        "src/api/routes/servers/audit_log.py",
        "src/api/routes/servers/webhooks.py",
        "src/api/routes/servers/icon_upload.py",
    }
)

_SERVER_ROUTES_DIR = ROOT / "src/api/routes/servers"

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _is_id_or_cursor(name: str) -> bool:
    return (
        name == "id"
        or name.endswith("_id")
        or name in {"cursor", "after", "before"}
    )


def _annotation_is_str(annotation) -> bool:
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


def _bare_int_on_param_violations(tree):
    """Return ``(lineno, handler, param)`` for bare int() on a boundary param."""
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_route_handler(node):
            continue
        args = node.args
        params = {
            arg.arg
            for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
            if _annotation_is_str(arg.annotation) and _is_id_or_cursor(arg.arg)
        }
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


def _iter_server_modules(base: Path | None = None):
    """Every shipped server route module, discovered structurally.

    The old ratchet iterated a hand-curated tuple of 7 paths. A new route
    module -- under any name -- that converts a string ID/cursor parameter with
    a bare ``int()`` was never examined and the guard passed silently. Walking
    the whole ``routes/servers`` package makes the sweep name-agnostic: no
    enumeration of known filenames bounds what gets checked.
    """
    base = base if base is not None else _SERVER_ROUTES_DIR
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
            )
        )
    return scanned


def _scan_tree():
    return _scan_paths(_iter_server_modules())


_SCAN = _scan_tree()


def test_detector_flags_bare_int_but_accepts_parse_id():
    """Self-test: only a bare int() on a str ID param is a violation."""
    bad = """
    @router.get("/bad")
    def bad(server_id: str, member_id: str, after: str):
        return int(server_id), int(member_id), int(after)
    """
    good = """
    @router.get("/good")
    def good(server_id: str):
        return parse_id(server_id, "server ID")
    """
    out_of_scope = """
    @router.get("/other")
    def other(count: int, label: str):
        return int(count), int(label)
    """
    assert len(_bare_int_on_param_violations(ast.parse(textwrap.dedent(bad)))) == 3
    assert _bare_int_on_param_violations(ast.parse(textwrap.dedent(good))) == []
    assert _bare_int_on_param_violations(ast.parse(textwrap.dedent(out_of_scope))) == []


def test_server_route_modules_use_parse_id_and_scan_is_not_vacuous():
    scanned = {rel for rel, *_ in _SCAN}
    assert all(rel.startswith("src/api/routes/servers/") for rel in scanned)
    # The curated adopters are a floor, not the scan bound: the structural walk
    # must reach every one of them and then some, so reverting to a fixed list
    # fails non-vacuously.
    assert _KNOWN_SERVER_ID_MODULES <= scanned, (
        "server sweep is missing known modules: "
        f"{sorted(_KNOWN_SERVER_ID_MODULES - scanned)}"
    )
    assert scanned - _KNOWN_SERVER_ID_MODULES, (
        "server sweep regressed to the old hand-curated module list"
    )
    # Every known adopter still calls the helper, and the scan really covers
    # route handlers -- otherwise the "no bare int()" assertion is vacuous.
    missing = [
        rel
        for rel, _, calls in _SCAN
        if rel in _KNOWN_SERVER_ID_MODULES and calls == 0
    ]
    assert not missing, f"parse_id missing from: {missing}"
    assert sum(calls for _, _, calls in _SCAN) >= 15


def test_no_server_route_converts_a_boundary_id_with_bare_int():
    offenders = [
        f"{rel}:{lineno} in {handler}({param})"
        for rel, violations, _ in _SCAN
        for lineno, handler, param in violations
    ]
    assert not offenders, (
        "Bare int() on a string ID/cursor server-route parameter reintroduced:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_novel_server_module_with_bare_int_is_flagged(tmp_path):
    """Non-vacuity: the structural sweep flags a brand-new, novel-named module.

    The old detector iterated a fixed list of 7 server paths, so a route module
    under any other name was invisible to the guard. This writes a never-before-
    seen module under a novel package name and proves the same discovery + scan
    pipeline the real guard uses reports its bare-int boundary conversion --
    with no advance knowledge of the file or handler name.
    """
    routes = tmp_path / "src/api/routes/servers/brand_new_subsystem"
    routes.mkdir(parents=True)
    module = routes / "future_server_export.py"

    def _scan_novel(source: str):
        module.write_text(textwrap.dedent(source), encoding="utf-8")
        return _scan_paths(
            _iter_server_modules(tmp_path / "src/api/routes/servers"), root=tmp_path
        )

    scanned = _scan_novel(
        """\
        @router.get("/future")
        def future_server_export(server_id: str):
            return int(server_id)
        """
    )
    assert len(scanned) == 1
    relative, violations, _ = scanned[0]
    assert relative == (
        "src/api/routes/servers/brand_new_subsystem/future_server_export.py"
    )
    assert violations, "novel server module's bare int() was not detected"

    # The same novel module using the shared gate is classified as clean.
    scanned = _scan_novel(
        """\
        @router.get("/future")
        def future_server_export(server_id: str):
            return parse_id(server_id, "server_id")
        """
    )
    assert scanned[0][1] == []


@pytest.mark.parametrize(
    "raw",
    [
        "١٢٣",  # Arabic-Indic digits
        "１２３",  # fullwidth digits
        "1_2",  # PEP 515 separator
        str(2**63),  # beyond the snowflake ceiling
        "0",  # zero is not a valid resource id
        "-1",  # negative
    ],
)
def test_shared_gate_rejects_what_bare_int_accepted(raw):
    from src.core.security.hardening import parse_id

    # Demonstrate the differential: int() used to accept these tokens while
    # the shared ASCII/range gate rejects them. Leading '+'/whitespace are
    # deliberately tolerated by bounded_int (legacy normalization) and are
    # therefore not part of this rejection set.
    int(raw)
    with pytest.raises(ValueError):
        parse_id(raw, "server ID")
