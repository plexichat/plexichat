"""Differential fuzz: random formulas through both engines.

The matrix in ``test_sum_cross_engine.py`` is systematic -- every operator and
adopted function over a fixed operand alphabet -- so it covers what somebody
enumerated. This adds the other half: a seeded stream of random doubles and
random text run through the operators and the text, math, logical, criteria and
date functions, evaluated in the server engine *and* the client engine until the
two disagree. It is what found the divergences no enumerated alphabet reached:
a negative fractional count truncated toward zero (``LEFT(text, -0.5)`` sliced
from the start), ``RIGHT`` past the end of the text reading from the end again,
``DATEVALUE`` years before 0100, and the Unicode decimal digits (``SUM("123")``
with Arabic-Indic digits was 123 here and 0 on the client).

The check is deterministic -- a fixed seed, and no clock or random function in
the generated formulas -- so a failure reproduces exactly. The client engine
lives in the other repository, so the formulas are handed to node (see
``cross_engine_probe.mjs``); without a client checkout or node the test skips,
exactly as the other differential checks do.

A number is compared with a small relative tolerance as well as the absolute
one: the transcendental functions (``SINH``, ``COSH``, ``EXP``, ...) call each
runtime's own libm, which can differ by one unit in the last place, and that is
not a semantic disagreement. Everything else -- text, booleans, errors, blanks
and the exact arithmetic -- is compared exactly.
"""

import math
import random
import shutil
from typing import Any, Dict, List

import pytest

from src.tests.suite.test_sum_cross_engine import (
    _client_engine,
    _client_results,
    _evaluate,
    _report,
)

# The fuzz is seeded, so a failure names the formula and both answers and does
# not depend on the run order.
SEED = 20260925
ITERATIONS = 1500

# The characters both runtimes case-fold and match identically: ASCII, a few
# accented letters, the sharp s and its capital, the micro sign and Greek mu,
# the sigma pair, the dotted/dotless i, and the wildcard/escape characters the
# criteria match on. The code points whose case mapping was added in a newer
# Unicode release than node's ICU (the Beria Erfe letters, the newest Latin
# ones) are deliberately absent: the runtime, not the engine, decides those, so
# they are documented rather than fuzzed.
_TEXT_ALPHABET = "abcABC 012.*?~-_ßẞåÅµμσςİıéÉ'!@#"

_BINARY_OPERATORS = ["+", "-", "*", "/", "^", "&", "=", "<>", "<", ">", "<=", ">="]
_ONE_ARG_FUNCTIONS = [
    "LEN",
    "UPPER",
    "LOWER",
    "TRIM",
    "CLEAN",
    "PROPER",
    "VALUE",
    "NUMBERVALUE",
    "ISNUMBER",
    "ISTEXT",
    "ISLOGICAL",
    "ISBLANK",
    "N",
    "T",
    "TYPE",
    "ABS",
    "SIGN",
    "INT",
    "TRUNC",
    "SQRT",
    "EXP",
    "LN",
    "LOG10",
    "SIN",
    "COS",
    "TAN",
    "ASIN",
    "ACOS",
    "ATAN",
    "SINH",
    "COSH",
    "TANH",
    "ERF",
    "ERFC",
    "FACT",
    "CHAR",
    "CODE",
    "ROUND",
    "DATEVALUE",
    "SUM",
    "AVERAGE",
    "MIN",
    "MAX",
]
_TWO_ARG_FUNCTIONS = [
    "POWER",
    "MOD",
    "SUM",
    "AVERAGE",
    "MIN",
    "MAX",
    "PRODUCT",
    "COUNT",
    "COUNTA",
    "EXACT",
    "FIND",
    "SEARCH",
    "SUBSTITUTE",
    "REPT",
    "CONCATENATE",
    "LARGE",
    "SMALL",
    "ROUND",
    "ROUNDUP",
    "ROUNDDOWN",
    "LEFT",
    "RIGHT",
    "COUNTIF",
    "SUMIF",
    "AVERAGEIF",
]
_THREE_ARG_FUNCTIONS = [
    "IF",
    "MID",
    "SUM",
    "AVERAGE",
    "MAX",
    "COUNT",
    "SUMIFS",
    "AVERAGEIFS",
]


def _random_number(rng: random.Random) -> str:
    """A random double as a literal both tokenizers read the same way.

    ``repr`` spells the shortest decimal that round-trips, which is also what
    JavaScript's ``Number`` reads, so the two engines start from the identical
    double. The magnitudes span small integers, ordinary fractions and the
    wide-exponent ends of the range (where the number-to-text rule lives).
    """
    roll = rng.random()
    if roll < 0.35:
        value = float(rng.randint(-50, 50))
    elif roll < 0.65:
        value = rng.uniform(-1e4, 1e4)
    else:
        value = math.ldexp(rng.random(), rng.randint(-20, 20)) * rng.choice([1, -1])
    return repr(value)


def _random_text(rng: random.Random) -> str:
    length = rng.randint(0, 8)
    text = "".join(rng.choice(_TEXT_ALPHABET) for _ in range(length))
    return '"' + text.replace('"', '""') + '"'


def _operand(rng: random.Random) -> str:
    return _random_number(rng) if rng.random() < 0.5 else _random_text(rng)


def _formulas() -> List[str]:
    rng = random.Random(SEED)
    formulas: List[str] = []
    for _ in range(ITERATIONS):
        roll = rng.random()
        if roll < 0.30:
            left, right = _operand(rng), _operand(rng)
            formulas.append(f"({left}){rng.choice(_BINARY_OPERATORS)}({right})")
        elif roll < 0.40:
            sign = rng.choice(["-", "+"])
            formulas.append(f"{sign}({_operand(rng)})")
        elif roll < 0.66:
            formulas.append(f"{rng.choice(_ONE_ARG_FUNCTIONS)}({_operand(rng)})")
        elif roll < 0.86:
            formulas.append(
                f"{rng.choice(_TWO_ARG_FUNCTIONS)}({_operand(rng)},{_operand(rng)})"
            )
        elif roll < 0.97:
            formulas.append(
                f"{rng.choice(_THREE_ARG_FUNCTIONS)}"
                f"({_operand(rng)},{_operand(rng)},{_operand(rng)})"
            )
        else:
            formulas.append(
                f"REPLACE({_operand(rng)},{_operand(rng)},"
                f"{_operand(rng)},{_operand(rng)})"
            )
    return formulas


def _fuzzy_same(ours: Dict[str, Any], theirs: Dict[str, Any]) -> bool:
    """Compare two typed results, tolerating a libm ulp on a number."""
    if ours["kind"] != theirs["kind"]:
        return False
    if ours["kind"] == "number":
        left, right = ours.get("value"), theirs.get("value")
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            return False
        return abs(left - right) <= 1e-9 + 1e-12 * max(abs(left), abs(right))
    return ours.get("value") == theirs.get("value")


def test_engines_agree_on_random_formulas():
    node = shutil.which("node")
    engine = _client_engine()
    if node is None or engine is None:
        pytest.skip(
            "cross-engine fuzz needs node and the plexichat-client checkout; "
            "set PLEXICHAT_CLIENT_PATH when the two are not siblings"
        )

    formulas = _formulas()
    ours = {formula: _evaluate(formula) for formula in formulas}
    theirs = _client_results(node, engine, formulas)
    divergences = [
        (formula, ours[formula], theirs[index])
        for index, formula in enumerate(formulas)
        if not _fuzzy_same(ours[formula], theirs[index])
    ]
    assert not divergences, _report(divergences, len(formulas))
