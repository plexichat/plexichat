"""Regression: DSAR audit-chain checksums are compared in constant time.

``DSARLog.verify_chain`` authenticates every append with either an HMAC-SHA256
(chain_key configured) or the legacy unkeyed SHA-256 digest. Comparing that
digest to the value stored in the log with ``!=`` short-circuits on the first
differing byte, giving a tamperer with repeated verification access a byte-by-
byte timing oracle for the digest. That undermines the tamper-evidence the
chain is supposed to provide. The verifier now uses ``hmac.compare_digest`` and
fails closed on a missing/malformed checksum.
"""

from __future__ import annotations

import ast
import copy
import json
import textwrap
from pathlib import Path

import pytest

from src.core.dsar.audit_log import DSARLog

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "core" / "dsar" / "audit_log.py"


def _find_function(tree, name):
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    return None


def _verify_chain_impl():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = _find_function(tree, "_verify_chain_impl")
    if fn is None:
        raise AssertionError("_verify_chain_impl not found")
    return fn


def _own_body_nodes(node: ast.AST):
    """Yield nodes on ``node``'s own execution path, skipping nested callables.

    Descend through statement-level nesting (``if``/``for``/``with``/``try``)
    but never into a nested function, async function or lambda: *defining* a
    helper that performs a constant-time comparison does not run it. Crediting
    such dead evidence lets a short-circuit checksum gate satisfy this
    regression test with a call that never executes.
    """
    stack = [node]
    while stack:
        for child in ast.iter_child_nodes(stack.pop()):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            yield child
            stack.append(child)


def _uses_compare_digest(fn):
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compare_digest"
        for node in _own_body_nodes(fn)
    )


def _short_circuit_checksum_comparisons(fn):
    offenders = []
    for node in _own_body_nodes(fn):
        if not isinstance(node, ast.Compare):
            continue
        left = ast.unparse(node.left)
        for comparator in node.comparators:
            right = ast.unparse(comparator)
            if "calculated_checksum" in (left, right) and "stored_checksum" in (
                left,
                right,
            ):
                offenders.append(f"line {node.lineno}: {left} ... {right}")
    return offenders


def test_checksum_uses_constant_time_comparison():
    """The checksum gate uses compare_digest, never a short-circuit ==/!=."""
    function = _verify_chain_impl()
    assert _uses_compare_digest(function), (
        "chain checksum gate has no in-handler hmac.compare_digest call"
    )
    offenders = _short_circuit_checksum_comparisons(function)
    assert not offenders, (
        "chain checksum compared with a short-circuit operator: " + "; ".join(offenders)
    )


def test_detectors_reject_dead_nested_compare_evidence():
    """compare_digest buried in a never-called nested callable is not evidence.

    Regression: both scans used ``ast.walk(function)``, which descends into
    nested functions and lambdas. A short-circuit gate could pass by parking a
    ``compare_digest`` call in code that is defined but never invoked.
    """
    dead = textwrap.dedent(
        """
        def _verify_chain_impl(self):
            def _never_called():
                return hmac.compare_digest(calculated_checksum, stored_checksum)
            return True
        """
    )
    fn = _find_function(ast.parse(dead), "_verify_chain_impl")
    assert not _uses_compare_digest(fn)

    dead = textwrap.dedent(
        """
        def _verify_chain_impl(self):
            helper = lambda: hmac.compare_digest(calculated_checksum, stored_checksum)
            return True
        """
    )
    fn = _find_function(ast.parse(dead), "_verify_chain_impl")
    assert not _uses_compare_digest(fn)

    # Live evidence behind statement-level nesting still satisfies the guard.
    live = textwrap.dedent(
        """
        def _verify_chain_impl(self):
            def _unrelated_helper():
                return 1
            try:
                with open(self.file_path) as f:
                    for line in f:
                        if line.strip():
                            return hmac.compare_digest(
                                calculated_checksum, stored_checksum
                            )
            except Exception:
                return False
        """
    )
    fn = _find_function(ast.parse(live), "_verify_chain_impl")
    assert _uses_compare_digest(fn)

    # A short-circuit compare in dead nested code is not an active timing
    # oracle; the same compare on the handler's own path is still flagged.
    dead = textwrap.dedent(
        """
        def _verify_chain_impl(self):
            def _never_called():
                return calculated_checksum != stored_checksum
            return True
        """
    )
    fn = _find_function(ast.parse(dead), "_verify_chain_impl")
    assert _short_circuit_checksum_comparisons(fn) == []

    live = textwrap.dedent(
        """
        def _verify_chain_impl(self):
            try:
                if True:
                    return calculated_checksum != stored_checksum
            except Exception:
                return False
        """
    )
    fn = _find_function(ast.parse(live), "_verify_chain_impl")
    assert _short_circuit_checksum_comparisons(fn)


def test_real_scan_is_not_vacuous_under_in_memory_mutation():
    """Mutating the real handler's compare_digest calls removes the evidence."""
    fn = _verify_chain_impl()
    assert _uses_compare_digest(fn)
    assert not _short_circuit_checksum_comparisons(fn)

    clone = copy.deepcopy(fn)
    mutated = 0
    for node in _own_body_nodes(clone):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "compare_digest"
            and isinstance(node.value, ast.Name)
            and node.value.id == "hmac"
        ):
            node.attr = "compare_digest_renamed"
            mutated += 1
    assert mutated > 0
    assert not _uses_compare_digest(clone)


def _rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_tampered_checksum_still_rejected(tmp_path):
    path = tmp_path / "dsar_audit.jsonl"
    log = DSARLog(file_path=str(path), chain_key="k" * 32, chain_key_id="k1")
    log.log_event(1, "EXPORTED", "dsar_request:1")
    assert log.verify_chain() == (True, 1, None)

    rows = _rows(path)
    rows[0]["checksum"] = "0" * 64
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    valid, _count, error = log.verify_chain()
    assert valid is False
    assert "checksum invalid" in error


@pytest.mark.parametrize("bad_checksum", [None, 12345, {"x": 1}, [1, 2], "\u00e9" * 64])
def test_malformed_checksum_fails_closed_without_raising(tmp_path, bad_checksum):
    """A log line with a non-string/missing checksum is a break, not a 500."""
    path = tmp_path / "dsar_audit.jsonl"
    log = DSARLog(file_path=str(path), chain_key="k" * 32, chain_key_id="k1")
    log.log_event(1, "EXPORTED", "dsar_request:1")

    rows = _rows(path)
    rows[0]["checksum"] = bad_checksum
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    valid, _count, error = log.verify_chain()
    assert valid is False
    assert "checksum invalid" in error
