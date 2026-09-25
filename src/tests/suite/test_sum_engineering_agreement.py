"""Differential check: the engineering quirks both engines share.

A few engineering behaviours deliberately differ from Excel: a fractional
``DEC2BIN`` input renders through the float base conversion (``DEC2BIN(5.9)``
is ``"101.9000000000000004"``), bitwise operands past 2**31 wrap the way
JavaScript's ``&``/``|``/``^`` do, and ``ERF``/``ERFC`` use the Abramowitz &
Stegun approximation. The shared case table pins the edge cases of each as the
value both engines agree on, and the engine README lists them under the known
divergences. This test is the broader live check: it runs a fixed battery of
engineering inputs -- the pinned edges, the fractional and out-of-range
operands, wrong arities and error arguments -- through the server engine *and*
the client engine and fails on a disagreement.

The client engine lives in the other repository, so this skips with a reason
when node or the client checkout is missing, exactly as the generated-matrix
check does.
"""

import shutil
from typing import Dict, List

import pytest

from src.core.suite.sum.formulas import FormulaEvaluator
from src.tests.suite.test_sum_cross_engine import (
    TABLE,
    _client_engine,
    _client_results,
    _resolver,
    _same,
    _typed,
)

# Inputs where the two engines deliberately differ from Excel but must agree
# with each other: fractional conversion inputs, large bitwise operands past
# the 32-bit wrap, blank inputs, and the float shift/places spellings.
FORMULAS: List[str] = [
    "DEC2BIN(5.9)",
    "DEC2HEX(255.9)",
    "DEC2OCT(8.9)",
    "DEC2BIN(-0.5)",
    "DEC2BIN(0.00001)",
    "DEC2BIN(1,3.5)",
    "BITAND(4294967296,4294967296)",
    "BITOR(4294967296,1)",
    "BITXOR(3000000000,0)",
    "BITAND(3000000000,3000000000)",
    "BITAND(5.5,2.5)",
    "BITLSHIFT(4,1.5)",
    "BITRSHIFT(5,1.5)",
    "BITLSHIFT(5.9,1)",
    "BITRSHIFT(7.9,1)",
    "BIN2DEC(,)",
    "DEC2BIN(,)",
    "BITAND(,3)",
    "DELTA(,)",
    "ERF(,)",
    "BIN2DEC(Z9)",
    "DEC2BIN(Z9)",
    "BITAND(Z9,3)",
    "DEC2BIN(-512)",
    "DEC2HEX(-549755813888)",
    "DEC2OCT(-536870912)",
    'HEX2BIN("FFFFFFFF00")',
    'OCT2HEX("7777777777")',
    'BIN2HEX("1111111111")',
    'CONVERT(1,"ft","in")',
    "BESSELY(2.4048,0)",
    "BESSELJ(10,3)",
    "ERF(-1.5)",
    "ERFC(2)",
]


def test_engines_agree_on_the_engineering_quirks():
    node = shutil.which("node")
    engine = _client_engine()
    if node is None or engine is None:
        pytest.skip(
            "engineering differential check needs node and the "
            "plexichat-client checkout; set PLEXICHAT_CLIENT_PATH when the "
            "two are not siblings"
        )
    ours: Dict[str, Dict[str, object]] = {
        formula: _typed(
            FormulaEvaluator(_resolver).evaluate_cell(TABLE["sheet"], 60, 60, formula)
        )
        for formula in FORMULAS
    }
    theirs = _client_results(node, engine, FORMULAS)
    divergences = [
        (formula, ours[formula], theirs[index])
        for index, formula in enumerate(FORMULAS)
        if not _same(ours[formula], theirs[index])
    ]
    assert not divergences, (
        f"{len(divergences)} engineering formulas answered differently "
        "(server vs client): "
        + "; ".join(
            f"{formula} server {ours} client {theirs}"
            for formula, ours, theirs in divergences[:10]
        )
    )
