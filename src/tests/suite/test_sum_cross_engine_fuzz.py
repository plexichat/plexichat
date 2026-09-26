"""Differential fuzz: random formulas through both engines.

The matrix in ``test_sum_cross_engine.py`` is systematic -- every operator and
adopted function over a fixed operand alphabet -- so it covers what somebody
enumerated. This adds the other half: a seeded stream of random doubles and
random text run through the operators and the text, math, logical, criteria and
date functions, evaluated in the server engine *and* the client engine until the
two disagree. It is what found the divergences no enumerated alphabet reached:
a negative fractional count truncated toward zero (``LEFT(text, -0.5)`` sliced
from the start) and ``RIGHT`` past the end of the text reading from the end
again. The audit's other findings have no random spelling to fuzz toward --
``DATEVALUE`` years before 0100, and the decimal digits outside ASCII that
neither engine reads as a number -- so the shared case table pins those by
hand.

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

from src.core.suite.sum import _formula_core as engine_core
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

# The Bessel functions get a stream of their own rather than riding the generic
# operand draw above: the interesting input is the *order*, which the generic
# alphabet almost never reaches past a few, and the argument magnitudes that
# separate the 40-term series' range from its overflow. Pulling from a separate
# seed keeps the formulas generated for every other function byte-identical to
# before this block was added.
BESSEL_SEED = 20260926
BESSEL_ITERATIONS = 400

# The power and root operators get a stream of their own for the same reason the
# Bessel pair did: the narrow corners are the *magnitudes*, and the generic
# draw above reaches the ends of the double range only occasionally and almost
# never as a base and an exponent that meet on an edge. The pairs here are
# built to land on them -- a base past one with an exponent big enough to
# overflow, a denormal under a root, a negative base under a fractional
# exponent -- which is where the two engines' libm and their overflow rules can
# part company. A separate seed keeps every other function's generated formulas
# byte-identical.
POWER_SEED = 20260927
POWER_ITERATIONS = 400

# The call contract gets a stream of its own for the same reason: the generic
# draw above only calls each function at a valid arity with everyday operands,
# so it never tried a stray argument (``ABS(1, 2)`` was silently 1 here while
# the client engine answers ``#VALUE!``), a fractional time serial
# (``SECOND`` rounded where the client truncates), a conversion whose number
# *and* places are bad (the client coerces places first), a computed error in
# a criteria range (an error cell only fails to match), or a lookup over a
# range holding an error cell. A separate seed keeps every other function's
# generated formulas byte-identical to before this block was added.
CONTRACT_SEED = 20260928
CONTRACT_TIME_ITERATIONS = 200

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
    # The date readers and the complex family: the serial's date part, and a
    # complex coefficient a double cannot hold, are both corners the enumerated
    # alphabet never reached.
    "DAY",
    "MONTH",
    "YEAR",
    "WEEKDAY",
    "HOUR",
    "MINUTE",
    "SECOND",
    "IMABS",
    "IMREAL",
    "IMAGINARY",
    "IMARGUMENT",
    "IMSQRT",
    "IMEXP",
    "IMLOG",
    "IMSINH",
    "IMCOSH",
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
    "DAYS",
    "IMSUM",
    "IMSUB",
    "IMPRODUCT",
    "IMDIV",
    "IMMOD",
    # The Bessel pair, so the generic operand draw also covers their arity and
    # the text/blank/logical coercion of both slots; the order-focused stream
    # below is what reaches the recurrence and overflow corners.
    "BESSELJ",
    "BESSELY",
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
    "TIME",
    "DATE",
    "IMSUM",
]


def _random_number(rng: random.Random) -> str:
    """A random double as a literal both tokenizers read the same way.

    ``repr`` spells the shortest decimal that round-trips, which is also what
    JavaScript's ``Number`` reads, so the two engines start from the identical
    double. The magnitudes span small integers, ordinary fractions, the
    wide-exponent middle of the range, and its ends -- the number-to-text rule
    and the complex overflow rule only show up at those ends.
    """
    roll = rng.random()
    if roll < 0.30:
        value = float(rng.randint(-50, 50))
    elif roll < 0.55:
        value = rng.uniform(-1e4, 1e4)
    elif roll < 0.90:
        value = math.ldexp(rng.random(), rng.randint(-20, 20)) * rng.choice([1, -1])
    else:
        value = math.ldexp(rng.random(), rng.choice([-300, 300])) * rng.choice([1, -1])
    return repr(value)


def _random_text(rng: random.Random) -> str:
    length = rng.randint(0, 8)
    text = "".join(rng.choice(_TEXT_ALPHABET) for _ in range(length))
    return '"' + text.replace('"', '""') + '"'


def _operand(rng: random.Random) -> str:
    return _random_number(rng) if rng.random() < 0.5 else _random_text(rng)


def _bessel_order(rng: random.Random) -> str:
    """A Bessel order: the small ones the series is exact at, the middle ones
    the Y recurrence has to climb to, the huge ones where J underflows to 0 and
    Y runs out of doubles, and a fractional one that both engines floor."""
    roll = rng.random()
    if roll < 0.40:
        return str(rng.randint(0, 12))
    if roll < 0.70:
        return str(rng.randint(0, 200))
    if roll < 0.85:
        return str(rng.randint(0, 5000))
    if roll < 0.95:
        return str(rng.choice([10000, 100000, 1000000, 1000000000]))
    return repr(rng.uniform(0, 12))


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


def _bessel_formulas() -> List[str]:
    """Bessel calls whose order and argument cover the series' whole range."""
    rng = random.Random(BESSEL_SEED)
    formulas: List[str] = []
    for _ in range(BESSEL_ITERATIONS):
        name = rng.choice(["BESSELJ", "BESSELY"])
        formulas.append(f"{name}({_random_number(rng)},{_bessel_order(rng)})")
    return formulas


def _power_operand(rng: random.Random) -> str:
    """A magnitude for the power/root stream.

    The small integers make a root exact, the fractions make a fractional
    exponent, and the powers of two at the ends of the range are the bases and
    exponents where a power overflows to ``#NUM!`` or a root underflows toward
    a denormal.
    """
    roll = rng.random()
    if roll < 0.25:
        return str(rng.randint(-10, 10))
    if roll < 0.45:
        return repr(rng.choice([0.5, 1.5, 2.0, 0.1, 0.9, -0.5, -2.0]))
    if roll < 0.70:
        return repr(rng.uniform(-1.0, 1.0))
    if roll < 0.85:
        return repr(math.ldexp(1.0, rng.randint(0, 1023)))
    return repr(math.ldexp(1.0, -rng.randint(0, 1074)))


def _power_formulas() -> List[str]:
    """Powers and roots whose bases and exponents meet the range's ends."""
    rng = random.Random(POWER_SEED)
    formulas: List[str] = []
    for _ in range(POWER_ITERATIONS):
        roll = rng.random()
        base, exponent = _power_operand(rng), _power_operand(rng)
        if roll < 0.40:
            formulas.append(f"POWER({base},{exponent})")
        elif roll < 0.70:
            formulas.append(f"({base})^({exponent})")
        elif roll < 0.85:
            formulas.append(f"SQRT({base})")
        else:
            formulas.append(f"({base})^(1/({exponent}))")
    return formulas


# The fixed-arity core functions a stray argument used to slip past: every
# one-argument name called with two, every two-argument name with three, and
# the two range functions that take an optional third argument called with
# four. The operand is always valid, so a wrong arity is the only thing that
# can fail -- which is what makes a silent extra argument visible. NOW and
# TODAY read the clock, so no call can pin them and they are absent here.
_CONTRACT_ARITY_NAMES = (
    "ABS",
    "ACOS",
    "ASIN",
    "ATAN",
    "COS",
    "DEGREES",
    "INT",
    "ISBLANK",
    "ISNUMBER",
    "ISTEXT",
    "LEN",
    "LOWER",
    "RADIANS",
    "SIN",
    "SQRT",
    "TAN",
    "TRIM",
    "UPPER",
    "CEILING",
    "COUNTIF",
    "FLOOR",
    "MOD",
    "POWER",
    "ROUND",
    "ROUNDDOWN",
    "ROUNDUP",
    "NETWORKDAYS",
    "WORKDAY",
)

# Conversions whose number *and* places are bad: the client coerces places
# first, so the answer is the places error rather than the number one.
_CONTRACT_ORDER_CALLS = [
    'BIN2OCT(18,"abc")',
    'BIN2OCT(18.985,"abc")',
    'BIN2HEX("101","y")',
    'HEX2BIN("12345678901","x")',
    'OCT2BIN("77777777777","z")',
    'DEC2BIN(5,"x")',
]

# Criteria calls over a computed error (which propagates) and over the error
# cell the shared table's grid holds in A5 (which only fails to match). The
# grid also holds 1..3 in A1:A3, so the range cases have cells to match.
_CONTRACT_RANGE_CALLS = [
    "COUNTIF(1/0,1)",
    "SUMIF(1/0,1)",
    "SUMIF(1,1,1/0)",
    "SUMIFS(1/0,1,1)",
    "COUNTIFS(1/0,1)",
    "COUNTIF(1/0,NA())",
    "SUMIF(NA(),1,1/0)",
    "COUNTIF(A5,1)",
    "SUMIF(A5,1)",
    "COUNTIF(A1:A5,2)",
    "SUMIF(A1:A5,1,A1:A5)",
    'COUNTIF(INDIRECT("A5"),1)',
]

# Lookups over the grid's A1:A5, which holds an error cell the lookup never
# touches, plus the computed-error keys that still propagate out of one.
_CONTRACT_LOOKUP_CALLS = [
    "INDEX(A1:A5,2)",
    "MATCH(2,A1:A5,0)",
    "LOOKUP(2,A1:A5)",
    "XLOOKUP(2,A1:A5,A1:A5)",
    "COUNTIF(OFFSET(A1,0,0,5,1),2)",
    "INDEX(A1:A3,1/0)",
    "MATCH(1/0,A1:A3,0)",
]


def _contract_formulas() -> List[str]:
    """The call-contract stream: wrong arities, time, order, ranges, lookups.

    The arity calls are systematic -- every fixed name at arities zero to
    four over the benign operand both engines must reject or compute
    identically -- while the time calls draw seeded fractional serials, the
    corners rounding used to miss. The order, range and lookup calls are the
    fixed formulas that found those divergences, held here so both engines
    keep agreeing on them.
    """
    formulas = [
        f"{name}({','.join(['1'] * arity)})"
        for name in _CONTRACT_ARITY_NAMES
        for arity in (0, 1, 2, 3, 4)
    ]
    # The adopted families the matrix sweeps at arities one to three get
    # their edges here: an empty call and a four-argument one.
    for _, names in (
        ("engineering", engine_core._ENGINEERING_FUNCTIONS),
        ("financial", engine_core._FINANCIAL_FUNCTIONS),
        ("statistical", engine_core._ADOPTED_STATISTICAL_FUNCTIONS),
        ("distribution", engine_core._DISTRIBUTION_FUNCTIONS),
        ("combinatorics", engine_core._COMBINATORICS_FUNCTIONS),
        ("complex", engine_core._COMPLEX_FUNCTIONS),
        ("single", engine_core._ADOPTED_SINGLE_FUNCTIONS - {"TODAY", "NOW"}),
    ):
        for name in sorted(names):
            formulas.append(f"{name}()")
            formulas.append(f"{name}(1,1,1,1)")
    rng = random.Random(CONTRACT_SEED)
    for _ in range(CONTRACT_TIME_ITERATIONS):
        serial = repr(rng.uniform(-100000, 100000) + rng.random())
        formulas.append(f"{rng.choice(['HOUR', 'MINUTE', 'SECOND'])}({serial})")
    formulas.extend(_CONTRACT_ORDER_CALLS)
    formulas.extend(_CONTRACT_RANGE_CALLS)
    formulas.extend(_CONTRACT_LOOKUP_CALLS)
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

    formulas = (
        _formulas() + _bessel_formulas() + _power_formulas() + _contract_formulas()
    )
    ours = {formula: _evaluate(formula) for formula in formulas}
    theirs = _client_results(node, engine, formulas)
    divergences = [
        (formula, ours[formula], theirs[index])
        for index, formula in enumerate(formulas)
        if not _fuzzy_same(ours[formula], theirs[index])
    ]
    assert not divergences, _report(divergences, len(formulas))
