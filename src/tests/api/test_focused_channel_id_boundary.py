"""Focused-channel ids must cross the shared ASCII snowflake boundary.

The ``_focused_channel`` setting carries caller-supplied JSON ids. Coercing
them with a bare ``int()`` accepts Unicode decimal homoglyphs, PEP-515
separators, signs and values past the 63-bit snowflake ceiling. Those values
are written to Valkey and merged into the caller's presence payload as
``current_channel_id``/``current_server_id``, which is broadcast to other users
and consumed by notification suppression. The shared ``parse_id`` gate is the
one boundary parser that rejects all of those forms, so both write paths must
delegate to it.

This is a static AST invariant (the pattern used by the other security suites
in this repo): it needs no app import, config, DB, Valkey or network, so it
cannot flake or be blocked by an unrelated import-time regression elsewhere.
The detector is validated against synthetic good/bad snippets before the real
tree is trusted, and a non-vacuity check proves the scanned functions really
contain both the focus handling and the gate calls.
"""

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[3]
SETTINGS = "src/api/routes/settings.py"

WRITE_PATHS = ("set_setting", "bulk_update_settings")


def _tree():
    return ast.parse((ROOT / SETTINGS).read_text(encoding="utf-8"))


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _call_names(node):
    """Names (bare or attribute) called anywhere under ``node``."""
    out = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name):
            out.add(func.id)
        elif isinstance(func, ast.Attribute):
            out.add(func.attr)
    return out


def _bare_int_on_focus_vars(node):
    """``int(cid)``/``int(sid)`` calls that reintroduce the parser differential."""
    hits = []
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "int"
            and sub.args
            and isinstance(sub.args[0], ast.Name)
            and sub.args[0].id in {"cid", "sid"}
        ):
            hits.append(sub.lineno)
    return hits


def _parse_focus_id_calls(node):
    return sum(
        1
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Name)
        and sub.func.id == "_parse_focus_id"
    )


def test_detector_catches_bare_int_and_accepts_gate():
    """Self-test: the detector flags only the regression shape."""
    bad = ast.parse(
        textwrap.dedent(
            """
        data = json.loads(value)
        cid = data.get("channel_id")
        presence.set_focused_channel(uid, channel_id=int(cid))
        """
        )
    )
    good = ast.parse(
        textwrap.dedent(
            """
        data = json.loads(value)
        cid = _parse_focus_id(data.get("channel_id"), "channel_id")
        presence.set_focused_channel(uid, channel_id=cid)
        """
        )
    )
    assert _bare_int_on_focus_vars(bad)
    assert _bare_int_on_focus_vars(good) == []
    assert _parse_focus_id_calls(good) == 1


def test_helper_delegates_to_shared_gate():
    """The helper must actually use parse_id, not reimplement int()."""
    tree = _tree()
    helper = _find_function(tree, "_parse_focus_id")
    assert helper is not None, "_parse_focus_id helper missing"
    assert "parse_id" in _call_names(helper)
    assert "int" not in _call_names(helper)

    module_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "parse_id" in module_names, "settings route stopped importing parse_id"


def test_scan_is_not_vacuous():
    """Both write paths really handle focus state and call the gate."""
    tree = _tree()
    for name in WRITE_PATHS:
        fn = _find_function(tree, name)
        assert fn is not None, f"{name} missing"
        names = _call_names(fn)
        assert "set_focused_channel" in names, f"{name} lost its presence write"
        assert "_parse_focus_id" in names, f"{name} lost the boundary gate"
        assert "json" in names or "loads" in names, f"{name} lost focus parsing"


@pytest.mark.parametrize("name", WRITE_PATHS)
def test_no_bare_int_conversion_on_focus_ids(name):
    tree = _tree()
    fn = _find_function(tree, name)
    assert _bare_int_on_focus_vars(fn) == [], f"{name} still uses int() on focus ids"
    # Both ids must be parsed -- a single gate on only one of them would leave
    # the other field exploitable.
    assert _parse_focus_id_calls(fn) >= 2, f"{name} does not gate both focus ids"
