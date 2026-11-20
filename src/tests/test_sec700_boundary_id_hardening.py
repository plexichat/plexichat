"""SEC700 invariant: HTTP boundary IDs go through the shared ASCII snowflake gate.

``parse_id`` (in ``src.core.security.hardening``) is the one boundary gate that
rejects Unicode digit homoglyphs, PEP-515 underscores, negatives and values past
``2**63 - 1``. Route modules that accept an id from a path/query must import and
call it; a bare ``int(<id_param>)`` at the boundary reintroduces the parser
differential and the oversized-id/offset bugs the gate was added to stop.

This is a static AST invariant: no app import, config, DB or network is needed.
The detectors are validated against synthetic good/bad snippets before the real
tree is trusted, and a non-vacuity test proves the configured modules really do
import and use the gate (so the scan cannot silently pass on missing code).
"""

import ast
import operator
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[2]

HARDENING_MODULE = "src.core.security.hardening"

# (source path, boundary id parameter names that must never be bare-int()ed)
ROUTE_MODULES = [
    ("src/api/routes/servers/server_crud.py", ("server_id", "new_owner_id")),
    ("src/api/routes/notifications.py", ("notification_id",)),
]

# The shared gate itself must stay ASCII-only and finite-range bounded.
GATE_MODULE = "src/core/security/hardening/numbers.py"


def _imports_parse_id(tree, from_module=HARDENING_MODULE):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == from_module:
            if any(alias.name == "parse_id" for alias in node.names):
                return True
    return False


def _parse_id_calls(tree):
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == "parse_id")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "parse_id")
        ):
            count += 1
    return count


def _bare_int_on_params(tree, param_names):
    """``(param, lineno)`` for every bare ``int(<boundary_param>)`` call."""
    names = set(param_names)
    hits = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "int"
        ):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name) and arg.id in names:
                hits.append((arg.id, node.lineno))
    return sorted(hits, key=lambda item: item[1])


def _string_constants(node):
    return {
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def _ascii_gate_patterns_ok(strings):
    """The ASCII digit class must be present and no permissive ``\\d`` used.

    ``\\d`` matches Unicode decimal digits in Python regexes, so even a single
    occurrence anywhere in this module would reintroduce the homoglyph parser
    differential. The check is intentionally strict across *all* string
    constants so a doc-comment cannot hide a regressed pattern next to a good
    one (the synthetic self-test covers both the missing-class and
    permissive-class cases).
    """
    for value in strings:
        if r"\d" in value:
            return False, f"permissive \\d in gate pattern: {value!r}"
    if not any("[0-9]" in s for s in strings):
        return False, "ASCII-only digit class missing"
    return True, "ok"


_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Pow: operator.pow,
}


def _const_value(node):
    """Evaluate a literal or simple numeric arithmetic expression."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _const_value(node.operand)
        if isinstance(value, (int, float)):
            return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _const_value(node.left), _const_value(node.right)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            try:
                return _BINOPS[type(node.op)](left, right)
            except (ArithmeticError, ValueError):
                return None
    return None


def _numeric_constants(tree):
    """name -> numeric value for simple module-level assignments."""
    out = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = _const_value(node.value)
        if isinstance(value, (int, float)):
            out[target.id] = value
    return out


def test_self_test_detectors_catch_bare_int_id_conversion():
    """A synthetic bare-int conversion (or a missing gate import) is caught."""
    good = textwrap.dedent(
        """
        from src.core.security.hardening import parse_id

        def get(server_id: str):
            sid = parse_id(server_id, "server_id")
            return lookup(sid)
        """
    )
    bad = textwrap.dedent(
        """
        def get(server_id: str):
            sid = int(server_id)
            return lookup(sid)
        """
    )
    mixed = textwrap.dedent(
        """
        from src.core.security.hardening import parse_id

        def get(server_id: str):
            other = parse_id(other_id, "other")
            sid = int(server_id)
            return lookup(sid)
        """
    )

    assert _imports_parse_id(ast.parse(good)) is True
    assert _imports_parse_id(ast.parse(bad)) is False
    assert _parse_id_calls(ast.parse(good)) == 1
    assert _bare_int_on_params(ast.parse(bad), {"server_id"}) == [("server_id", 3)]
    assert _bare_int_on_params(ast.parse(good), {"server_id"}) == []
    assert _bare_int_on_params(ast.parse(mixed), {"server_id"}) == [("server_id", 6)]


def test_self_test_gate_regex_detector():
    """The ASCII-regex detector rejects a reintroduced ``\\d`` class."""
    good = {"[+-]?[0-9]+", "some unrelated docstring"}
    bad = {"[+-]?\\d+"}
    mixed = {"[+-]?[0-9]+", "a helper that also uses \\d internally"}
    assert _ascii_gate_patterns_ok(good) == (True, "ok")
    for strings in (bad, mixed):
        ok, reason = _ascii_gate_patterns_ok(strings)
        assert ok is False and "permissive" in reason
    assert _ascii_gate_patterns_ok({"no digit class here"})[0] is False


def test_scan_is_not_vacuous():
    """The configured modules must actually import and use the shared gate."""
    assert len(ROUTE_MODULES) >= 2
    for rel, _names in ROUTE_MODULES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        assert _imports_parse_id(tree), f"{rel} lost the parse_id import"
        assert _parse_id_calls(tree) >= 1, f"{rel} has no parse_id use"


@pytest.mark.parametrize(
    "rel,names", ROUTE_MODULES, ids=[s[0].split("/")[-1] for s in ROUTE_MODULES]
)
def test_boundary_ids_never_use_bare_int(rel, names):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    offenders = _bare_int_on_params(tree, names)
    assert not offenders, f"{rel}: bare int() on boundary ids at {offenders}"


def test_self_test_numeric_constant_evaluator():
    tree = ast.parse("SNOWFLAKE_MAX = 2**63 - 1\nCAP = 256 * 1024")
    consts = _numeric_constants(tree)
    assert consts["SNOWFLAKE_MAX"] == 2**63 - 1
    assert consts["CAP"] == 262144


def test_shared_snowflake_gate_stays_ascii_and_bounded():
    tree = ast.parse((ROOT / GATE_MODULE).read_text(encoding="utf-8"))
    ok, reason = _ascii_gate_patterns_ok(_string_constants(tree))
    assert ok, f"shared gate: {reason}"
    consts = _numeric_constants(tree)
    assert consts.get("SNOWFLAKE_MAX") == 2**63 - 1, (
        "snowflake ceiling constant changed; parse_id range gate weakened"
    )
