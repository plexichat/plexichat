"""Regression: the first config write must be atomic and symlink-safe.

``ConfigLoader._save_config`` was hardened to mkstemp + os.replace, but the
initial creation path (``_create_default_config``, reached on every fresh boot
when no config exists) still used ``open(path, "w")``. That follows a symlink
planted at the config path and truncates the target before the contents are
known, so a local attacker could redirect the write to an arbitrary file or
leave a half-written config behind. These tests lock in the atomic writer.
"""

import ast
import json
from pathlib import Path

import pytest
import yaml

from src.utils.common_utils.utils.config.core import ConfigLoader

pytestmark = [pytest.mark.security, pytest.mark.unit]

CORE_PATH = Path(__file__).resolve().parents[3] / (
    "src/utils/common_utils/utils/config/core.py"
)


@pytest.mark.parametrize(
    "filename,loader", [("config.yaml", yaml.safe_load), ("config.json", json.load)]
)
def test_initial_write_does_not_follow_dangling_symlink(tmp_path, filename, loader):
    """A symlink at the config path must be replaced, not written through."""
    config_path = tmp_path / filename
    victim = tmp_path / "victim.out"  # does not exist: symlink is dangling
    config_path.symlink_to(victim)
    defaults = {"applications": {"webhook_signature_secret": "s3cr3t"}}

    ConfigLoader(str(config_path), default_config=defaults)

    # The attacker's target was never created and the link was replaced by a
    # regular file holding our config. The old open("w") path created `victim`
    # and left the symlink in place, so this test fails on the vulnerable code.
    assert not victim.exists(), "initial config write followed the symlink"
    assert not config_path.is_symlink(), "attacker symlink was not replaced"
    with open(config_path) as fh:
        assert loader(fh) == defaults


def test_initial_write_is_atomic_when_replace_fails(tmp_path, monkeypatch):
    """No partially written config (or temp file) may survive a failed rename."""
    import src.utils.common_utils.utils.config.core as core

    def fail_replace(src, dst):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(core.os, "replace", fail_replace)
    config_path = tmp_path / "config.yaml"

    with pytest.raises(OSError, match="simulated crash before rename"):
        ConfigLoader(str(config_path), default_config={"secret": "s3cr3t"})

    # The vulnerable writer never called os.replace, so it raised nothing and
    # left a truncated config file -- both assertions fail before the fix.
    assert not config_path.exists(), "half-written config was left behind"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _root_name(node):
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _own_body_nodes(root: ast.AST):
    """Yield ``root`` and nodes reachable through execution (not definition).

    Descend through statement-level nesting (if/for/with/try/...) but skip
    nested function and lambda bodies: *defining* a nested callable that calls
    mkstemp/fdopen/os.replace does not run it. Counting a dead nested primitive
    as protection lets a writer silence this invariant with dead evidence (the
    same vacuity fixed across the SEC100/200/300 route guards).
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _uses(function, attr, root=None):
    for node in _own_body_nodes(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == attr:
            if root is None or _root_name(func) == root:
                return True
    return False


def _direct_write_lines(function):
    lines = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open" and len(node.args) >= 2:
            mode = node.args[1]
            if (
                isinstance(mode, ast.Constant)
                and isinstance(mode.value, str)
                and "w" in mode.value
            ):
                lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr in (
            "write_text",
            "write_bytes",
        ):
            lines.append(node.lineno)
    return lines


def test_dead_nested_atomic_primitive_does_not_satisfy_uses():
    """An atomic primitive in a never-invoked nested callable is dead evidence.

    Regression: ``_uses`` used ``ast.walk(function)``, which descends into nested
    functions and lambdas. Merely *defining* a helper/lambda that calls
    os.replace did not run it, so a writer stripped of its live atomic primitive
    could silence this invariant with dead evidence. A live in-body primitive
    (including behind statement-level nesting) must still count.
    """
    dead_nested = """
def w(p):
    def never_called():
        os.replace(tmp, p)
"""
    dead_lambda = """
def w(p):
    _ = lambda: os.replace(tmp, p)
"""
    live_nested = """
def w(p):
    for _ in (1,):
        os.replace(tmp, p)
"""
    for source in (dead_nested, dead_lambda):
        fn = _find_function(ast.parse(source), "w")
        assert not _uses(fn, "replace", root="os"), (
            "dead nested os.replace must not protect the writer"
        )
    assert _uses(_find_function(ast.parse(live_nested), "w"), "replace", root="os")


def test_initial_writer_uses_atomic_primitives_and_detector_is_not_vacuous():
    """AST invariant: mkstemp + fdopen + os.replace, no direct truncate write."""
    tree = ast.parse(CORE_PATH.read_text(encoding="utf-8"))
    function = _find_function(tree, "_create_default_config")
    assert function is not None, "hardened writer dropped out of the tree"

    # Validate the detector against a synthetic vulnerable writer.
    bad = _find_function(
        ast.parse('def w(p):\n    with open(p, "w") as f:\n        f.write("x")\n'),
        "w",
    )
    assert _direct_write_lines(bad), "detector failed to see a direct open('w')"

    assert _uses(function, "mkstemp"), "no unique private temp file"
    assert _uses(function, "fdopen"), "temp fd is not wrapped"
    assert _uses(function, "replace", root="os"), "no atomic os.replace"
    assert _direct_write_lines(function) == [], "reintroduced a direct write"
