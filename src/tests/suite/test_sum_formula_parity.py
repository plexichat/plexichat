"""Cross-engine formula semantics, driven by the shared case table.

``formula_parity_cases.json`` is the contract the PlexiSum server engine and
the PlexiSum client engine are both held to. The byte-identical copy ships with
the client engine as ``tests/unit/data/formula-parity-cases.json``, and every
expected value is what Excel returns -- except for the divergences listed in the
engine README, where the expectation is the value both engines agree on.

Because each engine asserts against the same table, a change to one engine's
semantics that the other does not share fails here rather than drifting silently
until someone notices a different answer on screen.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

from src.core.suite.sum.formulas import FormulaEvaluator

TABLE_PATH = Path(__file__).parent / "formula_parity_cases.json"
TABLE = json.loads(TABLE_PATH.read_text())

# The two repositories each commit a copy of this file, and each engine tests
# itself against the copy it ships. Pinning the digest turns "the copies must
# stay byte-identical" from a comment into a test: edit either copy without
# mirroring it and the digest below stops matching, which fails *here* rather
# than leaving the two engines silently asserting different contracts.
TABLE_SHA256 = "b3b786dc65e23db8663a58f39d919e63bed8ccd7fb45c7a2ac70fc6e13337b0b"


def test_case_table_matches_the_shared_digest():
    digest = hashlib.sha256(TABLE_PATH.read_bytes()).hexdigest()
    assert digest == TABLE_SHA256, (
        "formula_parity_cases.json changed. Copy it byte-for-byte to "
        "plexichat-client/tests/unit/data/formula-parity-cases.json "
        f"and update TABLE_SHA256 to {digest} in both parity tests."
    )


def _parse_ref(ref: str) -> Tuple[int, int]:
    """Turn ``A5`` into a 0-based ``(row, column)`` pair."""
    letters = "".join(char for char in ref if char.isalpha()).upper()
    digits = "".join(char for char in ref if char.isdigit())
    column = 0
    for char in letters:
        column = column * 26 + (ord(char) - 64)
    return int(digits) - 1, column - 1


def _cell(ref: str, entry: Any) -> Dict[str, Any]:
    if isinstance(entry, dict):
        return {"value_type": "formula", "value": None, "formula": entry["formula"]}
    if isinstance(entry, bool):
        value_type = "boolean"
    elif isinstance(entry, (int, float)):
        value_type = "number"
    else:
        value_type = "text"
    return {"value_type": value_type, "value": entry, "formula": None}


_CELLS = {_parse_ref(ref): _cell(ref, entry) for ref, entry in TABLE["grid"].items()}


def _resolver(sheet: Optional[str], row: int, column: int) -> Optional[Dict[str, Any]]:
    return _CELLS.get((int(row), int(column)))


def _expected(raw: str) -> Any:
    """Decode a typed expectation from the table."""
    kind, _, literal = raw.partition(":")
    if kind == "number":
        return float(literal)
    if kind == "text":
        return literal
    if kind == "boolean":
        return literal == "true"
    if kind == "error":
        return literal
    if kind == "blank":
        return None
    raise AssertionError(f"unknown expectation {raw!r}")


@pytest.mark.parametrize(
    "case", TABLE["cases"], ids=[case["formula"] for case in TABLE["cases"]]
)
def test_engine_matches_the_shared_case_table(case):
    result = FormulaEvaluator(_resolver).evaluate_cell(
        TABLE["sheet"], 60, 60, case["formula"]
    )
    expected = _expected(case["expected"])
    if isinstance(expected, float):
        # A few cases are irrational (PI, RADIANS); the table stores them
        # rounded, so compare with a tolerance and require a real number.
        assert isinstance(result, (int, float)) and not isinstance(result, bool)
        assert result == pytest.approx(expected, abs=1e-9)
        return
    assert result == expected
