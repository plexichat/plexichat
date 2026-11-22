"""Server-route boundary IDs must use the ASCII/range-bounded snowflake gate.

The server route modules used to coerce their string path/query IDs with a bare
``int()``.  Python's ``int`` accepts Unicode decimal homoglyphs (``"١٢٣"``,
``"１２３"``), PEP 515 separators (``"1_2"``), signs, whitespace and values past
the 64-bit snowflake ceiling.  Every other route family (messaging, channels,
polls, suite, ...) already parses boundary IDs through
``src.core.security.hardening.parse_id``, so the same raw token was accepted by
a server route but rejected everywhere else -- a parser differential between
validation and consumption.

This suite ratchets the server route adopters: a refactor cannot silently bring
back a bare ``int()`` on an incoming ID/cursor parameter, and the shared gate is
proven to reject the exact inputs ``int`` used to let through.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

ROOT = Path(__file__).resolve().parents[3]

# Server route modules that now adopt the shared parse_id boundary helper.
HARDENED_SERVER_ID_MODULES = (
    "src/api/routes/servers/roles.py",
    "src/api/routes/servers/channels.py",
    "src/api/routes/servers/members.py",
    "src/api/routes/servers/misc.py",
    "src/api/routes/servers/audit_log.py",
    "src/api/routes/servers/webhooks.py",
    "src/api/routes/servers/icon_upload.py",
)

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


def _scan_tree():
    scanned = []
    for relative in HARDENED_SERVER_ID_MODULES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        scanned.append((relative, _bare_int_on_param_violations(tree), _parse_id_calls(tree)))
    return scanned


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
    assert len(_SCAN) == len(HARDENED_SERVER_ID_MODULES)
    missing = [rel for rel, _, calls in _SCAN if calls == 0]
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
