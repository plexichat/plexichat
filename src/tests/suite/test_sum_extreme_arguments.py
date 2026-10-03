"""The numeric extremes that used to hang or raise, pinned to their answers.

A formula is untrusted input. The depreciation period, the binomial success
count and the Poisson event count are unrestricted doubles, and the original
implementations walked a loop once per unit of them: ``DB(1000,100,10,1e9)``,
``BINOMDIST(1e9,1e9,0.5,TRUE)`` and ``POISSON(1e9,1,TRUE)`` ran for hours. The
inverse distributions were worse -- ``GAMMAINV`` and ``BETAINV`` raised a
``ZeroDivisionError`` (a denominator whose subtraction rounded away) or an
``OverflowError`` straight out of the evaluator, where the client engine
answered ``#NUM!``.

Each loop is bounded now by the shared ``MAX_LOOP_ITERATIONS`` and the numeric
families map an arithmetic overflow to ``#NUM!`` the way the client engine's
helpers do, so every call below returns promptly with the value pinned here.
An unbounded loop would trip the suite's ``--timeout`` on the case rather than
passing quietly, which is the same guarantee ``test_sum_cross_engine`` gets
from running the arguments through both engines and comparing them.
"""

import pytest

from src.tests.suite.test_sum_cross_engine import _evaluate

# Each answer is written the way the shared table writes one -- ``kind:value``
# -- and a finite number is compared with a tolerance.
_CASES = [
    # A period whose depreciation underflows keeps the zero the loop settled on
    # instead of counting out the remaining periods.
    ("DB(1000,100,10,1000000000)", "number:0"),
    ("DB(1000,100,10,1e18)", "number:0"),
    # A period past the iteration bound is #NUM! rather than a hang.
    ("DB(1000,100,1000000000,1000000000)", "error:#NUM!"),
    ("DB(1000,1001,10,1e9)", "error:#NUM!"),
    # ...but a period at the bound still computes.
    ("DB(1000,100,1000000000,100000)", "number:2.302054999082164e-06"),
    ("DB(1000,100,1000000000,5)", "number:2.302585101363495e-06"),
    # The cumulative distributions cap their summed terms.
    ("BINOMDIST(1000000000,1000000000,0.5,TRUE)", "error:#NUM!"),
    ("BINOMDIST(170,1e18,0.5,TRUE)", "error:#NUM!"),
    ("BINOMDIST(100000,100000,0.5,TRUE)", "error:#NUM!"),
    ("POISSON(1000000000,1,TRUE)", "error:#NUM!"),
    ("POISSON(1,1000000000,TRUE)", "number:0"),
    ("POISSON(100000,1,TRUE)", "number:1"),
    # The inverse distributions answer #NUM! where a factor overflows instead
    # of raising, or converging on whatever the overflow left behind.
    ("GAMMAINV(0.5,1e18,1e18)", "number:9.999999999708962e+35"),
    ("GAMMAINV(0.5,1e300,1e300)", "error:#NUM!"),
    ("BETAINV(0.5,1e300,1e300)", "error:#NUM!"),
    ("BETAINV(0.5,1e18,1e18)", "number:0.49999999999954525"),
    # The counting functions stop at the double overflow rather than looping.
    ("FACTDOUBLE(1000000000)", "error:#NUM!"),
    ("FACT(1000000000)", "error:#NUM!"),
    ("COMBIN(1000000000,500000000)", "error:#NUM!"),
    ("PERMUT(1000000000,500000000)", "error:#NUM!"),
    ("MULTINOMIAL(1000000000,1000000000)", "error:#NUM!"),
    ("COMBINA(1000000000,1000000000)", "error:#NUM!"),
    # OFFSET's height and width are loop bounds too, and unrestricted doubles:
    # a huge span used to build a block of 10^18 cells and stall the thread.
    # A span past the shared MAX_BLOCK_CELLS is #NUM!, the same answer the
    # client engine gives.
    ("OFFSET(A1,0,0,100000,100000)", "error:#NUM!"),
    ("OFFSET(A1,0,0,1e9,1e9)", "error:#NUM!"),
    ("OFFSET(A1,0,0,1e9,1)", "error:#NUM!"),
]


@pytest.mark.parametrize("formula,expected", _CASES, ids=[case[0] for case in _CASES])
def test_extreme_arguments_match_the_pinned_answer(formula, expected):
    kind, _, literal = expected.partition(":")
    result = _evaluate(formula)
    assert result["kind"] == kind, f"{formula} -> {result!r}, expected {expected!r}"
    if kind == "number":
        assert result["value"] == pytest.approx(float(literal), abs=1e-9)
    elif kind != "blank":
        assert result["value"] == literal
