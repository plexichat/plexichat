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
import json
from pathlib import Path

import pytest

from src.core.dsar.audit_log import DSARLog

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "core" / "dsar" / "audit_log.py"


def _verify_chain_impl():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_verify_chain_impl"
        ):
            return node
    raise AssertionError("_verify_chain_impl not found")


def test_checksum_uses_constant_time_comparison():
    """The checksum gate uses compare_digest, never a short-circuit ==/!=."""
    function = _verify_chain_impl()
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compare_digest"
        for node in ast.walk(function)
    )

    offenders = []
    for node in ast.walk(function):
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
    assert not offenders, (
        "chain checksum compared with a short-circuit operator: " + "; ".join(offenders)
    )


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
