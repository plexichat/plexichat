"""Differential check: one formula set, both engines, live.

The shared case table pins the answers both engines must give, but only for the
cases somebody thought to write down. This test covers the other half of the
contract: it builds a systematic matrix of comparisons, criteria and lookups,
evaluates every formula in the server engine *and* in the client engine, and
fails when the two disagree -- so a semantic change on one side shows up as a
divergence instead of waiting for a case to be pinned.

The client engine lives in the other repository, so the formulas are handed to
node (see ``cross_engine_probe.mjs``). Without a client checkout or node the
test skips: the two engines are separate checkouts, and the test says so rather
than pretending it ran. Point ``PLEXICHAT_CLIENT_PATH`` at the client when the
two are not siblings.

Nothing here needs pinning by hand: a disagreement prints the formula and both
answers, and a legitimate difference belongs in the shared case table, where
both engines are then held to it.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.core.suite.sum.formulas import FormulaEvaluator

TABLE_PATH = Path(__file__).parent / "formula_parity_cases.json"
PROBE_PATH = Path(__file__).parent / "cross_engine_probe.mjs"
ENGINE_ENTRY = Path("src/js/suite/apps/plexisum/formula-engine/index.js")
TABLE = json.loads(TABLE_PATH.read_text())

# The operand alphabet the comparison matrix crosses with the operators: both
# cases of one word (the `=` rule), text that reads as a number, the characters
# the pattern rules turn on, both logicals, empty text, and references into the
# fixture grid -- a number, a logical and a blank cell.
_OPERANDS = [
    "0",
    "1",
    "2",
    '"a"',
    '"A"',
    '"b"',
    '"2"',
    '"tw*"',
    '"*"',
    '""',
    "TRUE",
    "FALSE",
    "A1",
    "B6",
    "Z1",
]
_OPERATORS = ["=", "<>", "<", ">", "<=", ">="]

# The criteria alphabet crossed with four ranges, including the cells holding a
# literal `*`, `?` and `~` that the escape rule is about.
_CRITERIA_PAIRS = [
    ("A1:A3", "C1:C3"),
    ("D1:D3", "C1:C3"),
    ("B1:B3", "C1:C3"),
    ("L1:L4", "M1:M4"),
]
_CRITERIA = [
    "1",
    "0",
    '"a"',
    '"A"',
    '"ap*"',
    '"tw~*"',
    '"~*"',
    '"<>1"',
    '">1"',
    '""',
    "TRUE",
]

# Lookup keys -- text, a number, a logical and patterns -- against a text
# vector, a numeric vector and the wildcard cells, exact and approximate.
_LOOKUP_KEYS = ["1", "2", '"a"', '"tw*"', '"tw~*"', '"~*"', '"ap*"', "TRUE"]
_LOOKUP_RANGES = ["D1:D3", "B1:B3", "A1:A3", "L1:L4"]


def _formulas() -> List[str]:
    """Build the matrix: comparisons, then criteria calls, then lookups."""
    formulas: List[str] = []
    for left in _OPERANDS:
        for right in _OPERANDS:
            for operator in _OPERATORS:
                formulas.append(f"{left}{operator}{right}")
    for criteria in _CRITERIA:
        for criteria_range, sum_range in _CRITERIA_PAIRS:
            formulas.append(f"COUNTIF({criteria_range},{criteria})")
            formulas.append(f"SUMIF({criteria_range},{criteria},{sum_range})")
            formulas.append(f"AVERAGEIF({criteria_range},{criteria},{sum_range})")
    for key in _LOOKUP_KEYS:
        for lookup_range in _LOOKUP_RANGES:
            formulas.append(f"MATCH({key},{lookup_range},0)")
            formulas.append(f"MATCH({key},{lookup_range},1)")
            formulas.append(f"VLOOKUP({key},{lookup_range},1,FALSE)")
    return formulas


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


def _typed(value: Any) -> Dict[str, Any]:
    """Encode a result the way both sides compare it.

    The kinds keep a text ``"1"`` apart from the number 1 and a blank apart
    from the empty text ``""``, which is the distinction the two engines have
    disagreed about before.
    """
    if isinstance(value, bool):
        return {"kind": "boolean", "value": value}
    if isinstance(value, (int, float)):
        return {"kind": "number", "value": float(value)}
    if value is None:
        return {"kind": "blank"}
    if isinstance(value, str) and value.startswith("#"):
        return {"kind": "error", "value": value}
    return {"kind": "text", "value": value}


def _same(ours: Dict[str, Any], theirs: Dict[str, Any]) -> bool:
    if ours["kind"] != theirs["kind"]:
        return False
    if ours["kind"] == "number":
        return abs(ours["value"] - theirs["value"]) < 1e-9
    return ours.get("value") == theirs.get("value")


def _describe(result: Dict[str, Any]) -> str:
    if result["kind"] == "blank":
        return "blank"
    return f"{result['kind']}:{result['value']}"


def _client_engine() -> Optional[Path]:
    """Locate the client engine, from the environment or a sibling checkout."""
    roots = []
    configured = os.environ.get("PLEXICHAT_CLIENT_PATH")
    if configured:
        roots.append(Path(configured))
    roots.append(Path(__file__).resolve().parents[4] / "plexichat-client")
    for root in roots:
        entry = root / ENGINE_ENTRY
        if entry.is_file():
            return entry
    return None


def _client_results(
    node: str, engine: Optional[Path], formulas: List[str]
) -> List[Dict[str, Any]]:
    """Run the matrix through the client engine under node."""
    request = json.dumps({"table": str(TABLE_PATH), "formulas": formulas})
    completed = subprocess.run(
        [node, str(PROBE_PATH), str(TABLE_PATH), str(engine)],
        input=request,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, "the client probe failed:\n" + (
        completed.stderr or completed.stdout
    )
    results = json.loads(completed.stdout)["results"]
    assert len(results) == len(formulas), "the client probe lost a result"
    return results


def _report(
    divergences: List[Tuple[str, Dict[str, Any], Dict[str, Any]]], total: int
) -> str:
    lines = [
        f"{len(divergences)} of {total} generated formulas answered differently "
        "(server vs client):"
    ]
    for formula, ours, theirs in divergences[:25]:
        lines.append(
            f"  {formula}   server {_describe(ours)}   client {_describe(theirs)}"
        )
    if len(divergences) > 25:
        lines.append(f"  ... and {len(divergences) - 25} more")
    lines.append(
        "Add the agreed answer to the shared case table, or fix the engine that "
        "is wrong."
    )
    return "\n".join(lines)


def test_engines_agree_on_a_generated_matrix():
    node = shutil.which("node")
    engine = _client_engine()
    if node is None or engine is None:
        pytest.skip(
            "cross-engine check needs node and the plexichat-client checkout; "
            "set PLEXICHAT_CLIENT_PATH when the two are not siblings"
        )

    formulas = _formulas()
    ours = {
        formula: _typed(
            FormulaEvaluator(_resolver).evaluate_cell(TABLE["sheet"], 60, 60, formula)
        )
        for formula in formulas
    }
    theirs = _client_results(node, engine, formulas)
    divergences = [
        (formula, ours[formula], theirs[index])
        for index, formula in enumerate(formulas)
        if not _same(ours[formula], theirs[index])
    ]
    assert not divergences, _report(divergences, len(formulas))
