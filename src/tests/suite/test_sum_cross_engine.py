"""Differential check: one formula set, both engines, live.

The shared case table pins the answers both engines must give, but only for the
cases somebody thought to write down. This test covers the other half of the
contract: it builds a systematic matrix -- every comparison and arithmetic
operation over an operand alphabet, the criteria functions over four ranges,
the text and date functions over their own operands, the lookups over four
vectors, the structured family over its ranges, the branching family over its
keys, the scalar and criteria slots of the range functions over cell
references, and the adopted engineering, financial, statistical, distribution,
combinatorics and complex families each at arities one to three -- evaluates
every formula in the server engine *and* in the client engine, and fails when
the two disagree. A semantic change on one side shows up as a divergence
instead of waiting for a case to be pinned. The table's own cases are compared
engine-to-engine as well (see
``test_the_engines_agree_on_every_shared_case``), so a loose comparison in
either repository's parity test cannot hide a disagreement here.

The client engine lives in the other repository, so the formulas are handed to
node (see ``cross_engine_probe.mjs``). Without a client checkout or node the
test skips: the two engines are separate checkouts, and the test says so rather
than pretending it ran. Point ``PLEXICHAT_CLIENT_PATH`` at the client when the
two are not siblings.

This engine implements a deliberate subset of the client's functions, so the
matrix is grouped by function and a group whose name this engine does not know
is left out (see ``_missing``); the groups it leaves out are pinned, so losing
a function shows up here rather than quietly shrinking the matrix.

Nothing here needs pinning by hand: a disagreement prints the formula and both
answers, and a legitimate difference belongs in the shared case table, where
both engines are then held to it.
"""

import json
import math
import os
import shutil
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from src.core.suite.sum import _formula_core as engine_core
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

# The numeric alphabet the arithmetic operators cross: whole numbers, a
# fraction, text that reads as a number and text that does not, logicals, two
# cells and a reference to an error cell.
_NUMBERS = [
    "0",
    "1",
    "2",
    "-1",
    "1.5",
    '"2"',
    '"2abc"',
    "TRUE",
    "FALSE",
    "A1",
    "B6",
    "Z1",
]
_ARITHMETIC = ["+", "-", "*", "/", "^"]

# One call per text function, run over the same operands. The operators are
# here too: `&` concatenates, and the nested call exercises composition. The
# astral operand (two emoji, four UTF-16 units) holds the units rule: Excel
# and the client engine measure text in UTF-16 code units, so LEN answers 4
# and LEFT/RIGHT/MID/FIND/SEARCH/REPLACE/CODE cut and count in units rather
# than in the code points Python indexes. Counting in code points answered
# LEN("😀") as 1 here while the client answered 2.
_TEXT_OPERANDS = [
    '"a"',
    '"A"',
    '"abc"',
    '"AbC"',
    '" a "',
    '"2"',
    '"2abc"',
    '""',
    '"1.5"',
    '"😀😃"',
    "A1",
    "B1",
    "D1",
    "F1",
    "F2",
    "K1",
    "L1",
]
_TEXT_CALLS = [
    ("LEN", "LEN({x})"),
    ("UPPER", "UPPER({x})"),
    ("LOWER", "LOWER({x})"),
    ("TRIM", "TRIM({x})"),
    ("CLEAN", "CLEAN({x})"),
    ("PROPER", "PROPER({x})"),
    ("LEFT", "LEFT({x},2)"),
    ("RIGHT", "RIGHT({x},2)"),
    ("MID", "MID({x},2,3)"),
    ("FIND", 'FIND("a",{x})'),
    ("FIND uppercase", 'FIND("A",{x})'),
    ("SEARCH", 'SEARCH("a",{x})'),
    ("SEARCH uppercase", 'SEARCH("A",{x})'),
    ("SUBSTITUTE", 'SUBSTITUTE({x},"a","b")'),
    ("REPLACE", 'REPLACE({x},2,2,"z")'),
    ("REPT", "REPT({x},2)"),
    ("EXACT", 'EXACT({x},"A")'),
    ("VALUE", "VALUE({x})"),
    ("NUMBERVALUE", "NUMBERVALUE({x})"),
    ("TEXT", 'TEXT({x},"0.00")'),
    ("CHAR", "CHAR(65)"),
    ("CODE", "CODE({x})"),
    ("CONCATENATE", 'CONCATENATE({x},"!")'),
    ("&", '{x}&"!"'),
    ("T", "T({x})"),
    ("composed", "LEN(TRIM(CLEAN({x})))"),
]

# The fixture's date serials, a couple of literals, and text that reads as a
# date. TODAY and NOW are deliberately absent: they read the clock, so the two
# engines cannot be compared on them.
# The negative and positive fractions hold the date readers to the same
# serial rule: the fraction of a serial is a time of day and never moves the
# date, so DAY(-2.5) is 28 on both sides. Reading the raw fraction put the
# client half a day into 1899-12-27 and answered 27 there.
_DATE_OPERANDS = ["H1", "H2", "H6", "0", "39448", "-1", "-2.5", "2.5", '"2023-01-01"']
_DATE_CALLS = [
    ("YEAR", "YEAR({x})"),
    ("MONTH", "MONTH({x})"),
    ("DAY", "DAY({x})"),
    ("HOUR", "HOUR({x})"),
    ("MINUTE", "MINUTE({x})"),
    ("SECOND", "SECOND({x})"),
    ("WEEKDAY", "WEEKDAY({x})"),
    ("WEEKDAY,2", "WEEKDAY({x},2)"),
    ("WEEKNUM", "WEEKNUM({x})"),
    ("WEEKNUM,2", "WEEKNUM({x},2)"),
    ("EOMONTH", "EOMONTH({x},0)"),
    ("EOMONTH,+1", "EOMONTH({x},1)"),
    ("EDATE", "EDATE({x},1)"),
    ("EDATE,-1", "EDATE({x},-1)"),
    ("NETWORKDAYS", "NETWORKDAYS({x},H2)"),
    ("WORKDAY", "WORKDAY({x},10)"),
]
_DATE_EXTRAS = [
    "DATE(2024,2,29)",
    "DATE(2024,13,1)",
    "DATE(2023,2,29)",
    "DATE(0,1,1)",
    "DATE(9999,12,31)",
    "TIME(25,0,0)",
    "TIME(1,2,3)",
    "TIME(-1,0,0)",
    "DATE(2024,3,1)-DATE(2024,2,1)",
    'DATEVALUE("2023-01-01")',
    'DATEVALUE("12/31/2023")',
    'DATEVALUE("nope")',
    "H1+1",
    "H2-H1",
]

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
_CRITERIA_CALLS = [
    ("COUNTIF", "COUNTIF({r},{c})"),
    ("SUMIF", "SUMIF({r},{c},{s})"),
    ("AVERAGEIF", "AVERAGEIF({r},{c},{s})"),
]

# Lookup keys -- text, a number, a logical and patterns -- against a text
# vector, a numeric vector and the wildcard cells. LOOKUP is here to hold the
# two engines together on the approximate form, which does not take wildcards.
_LOOKUP_KEYS = ["1", "2", '"a"', '"tw*"', '"tw~*"', '"~*"', '"ap*"', "TRUE"]
_LOOKUP_RANGES = ["D1:D3", "B1:B3", "A1:A3", "L1:L4"]
_LOOKUP_CALLS = [
    ("MATCH exact", "MATCH({k},{r},0)"),
    ("MATCH approx", "MATCH({k},{r},1)"),
    ("VLOOKUP exact", "VLOOKUP({k},{r},1,FALSE)"),
    ("VLOOKUP approx", "VLOOKUP({k},{r},1,TRUE)"),
    ("LOOKUP", "LOOKUP({k},{r})"),
    ("XLOOKUP exact", "XLOOKUP({k},{r},{r})"),
    ("XLOOKUP not-found", 'XLOOKUP({k},{r},{r},"nf")'),
    ("XLOOKUP next larger", 'XLOOKUP({k},{r},{r},"nf",1)'),
    ("XLOOKUP next smaller", 'XLOOKUP({k},{r},{r},"nf",-1)'),
    ("XLOOKUP wildcard", 'XLOOKUP({k},{r},{r},"nf",2)'),
    ("XLOOKUP reverse", 'XLOOKUP({k},{r},{r},"nf",0,-1)'),
]

# The scalar slot of the range functions, crossed with cell references and
# literals. The evaluator hands these functions their arguments unevaluated, so
# a scalar or a criterion written as a cell has to be *read from that cell*
# before the function can use it. A number, numeric text, plain text, a
# logical, a blank, a zero and an error cell cover the coercion, the skip and
# the propagation. Both defects this group found were here: a raw argument node
# fed to a coercion helper answered #VALUE! (LARGE(A1:A3, A1)), and a criterion
# still in node form matched nothing at all (COUNTIF(A1:A3, A1) was 0).
_RANGE_KEYS = [
    "A1",
    "A4",
    "A5",
    "A6",
    "B6",
    "I1",
    "K3",
    "Z1",
    "M1",
    "1",
    "0.5",
    '"2"',
    "TRUE",
    '"a"',
]
_RANGE_CALLS = [
    ("criteria SUMIF", "SUMIF(A1:A3,{k},C1:C3)"),
    ("criteria COUNTIF", "COUNTIF(A1:A3,{k})"),
    ("criteria AVERAGEIF", "AVERAGEIF(A1:A3,{k},C1:C3)"),
    ("criteria SUMIFS", "SUMIFS(C1:C3,A1:A3,{k})"),
    ("criteria COUNTIFS", "COUNTIFS(A1:A3,{k})"),
    ("criteria AVERAGEIFS", "AVERAGEIFS(C1:C3,A1:A3,{k})"),
    ("LARGE k", "LARGE(A1:A3,{k})"),
    ("SMALL k", "SMALL(A1:A3,{k})"),
    ("RANK number", "RANK({k},A1:A3)"),
    ("RANK order", "RANK(A1,A1:A3,{k})"),
    ("PERCENTILE k", "PERCENTILE(A1:A3,{k})"),
    ("QUARTILE quart", "QUARTILE(A1:A3,{k})"),
    ("TRIMMEAN percent", "TRIMMEAN(A1:A3,{k})"),
    ("PERCENTRANK significance", "PERCENTRANK(A1:A3,A1,{k})"),
    ("STANDARDIZE x", "STANDARDIZE({k},1,2)"),
    ("FORECAST x", "FORECAST({k},A1:A3,C1:C3)"),
]

# The structured family takes a range plus a scalar, which the generic family
# generator below cannot express: it passes the same operand to every slot, and
# a scalar is not a range. Each is called over the text, numeric and
# pattern-holding ranges, so the family's own pairing rule -- text is dropped
# per side, and the survivors have to pair up -- is compared on both engines.
_STRUCTURED_RANGES = ["A1:A3", "C1:C3", "D1:D3", "F1:F3", "L1:L4"]
_STRUCTURED_CALLS = [
    ("CORREL", "CORREL({r},C1:C3)"),
    ("RSQ", "RSQ({r},C1:C3)"),
    ("SLOPE", "SLOPE({r},C1:C3)"),
    ("INTERCEPT", "INTERCEPT({r},C1:C3)"),
    ("STEYX", "STEYX({r},C1:C3)"),
    ("SUMX2MY2", "SUMX2MY2({r},C1:C3)"),
    ("SUMX2PY2", "SUMX2PY2({r},C1:C3)"),
    ("SUMXY", "SUMXY({r},C1:C3)"),
    ("LARGE", "LARGE({r},1)"),
    ("SMALL", "SMALL({r},1)"),
    ("QUARTILE", "QUARTILE({r},1)"),
    ("PERCENTILE", "PERCENTILE({r},0.5)"),
    ("PERCENTRANK", "PERCENTRANK({r},1,3)"),
    ("RANK", "RANK(1,{r})"),
    ("TRIMMEAN", "TRIMMEAN({r},0.2)"),
]

# The branching family picks a branch or an argument, so the same key serves as
# the index, the condition's operand and SWITCH's expression.
_BRANCHING_CALLS = [
    ("CHOOSE index", 'CHOOSE({k},"a","b","c")'),
    ("CHOOSE values", "CHOOSE(1,{k},B1,C1)"),
    ("IFS", 'IFS({k}>1,"hi",TRUE,"lo")'),
    ("IFS zero", 'IFS({k}<0,"neg",{k}=0,"zero")'),
    ("SWITCH", 'SWITCH({k},1,"one",2,"two","other")'),
    ("SWITCH text", 'SWITCH({k},"a","A","b","B","z")'),
    ("SWITCH default", 'SWITCH({k},1,"one","d")'),
]


def _product(alphabet: str, length: int) -> List[Tuple[str, ...]]:
    """All ``length``-tuples over ``alphabet``, for the sign combinations."""
    if length == 0:
        return [()]
    return [
        (char,) + tail for char in alphabet for tail in _product(alphabet, length - 1)
    ]


# The families this engine adopted from the client wholesale. The names come
# from this engine's own dispatch sets, so no group is missing on this side, and
# each function is called at arities one to three over one numeric alphabet. A
# wrong arity answers #VALUE! on both sides, which is agreement; what the sweep
# is for is the calls that do not. It found two: NPV at a rate of -1 divided by
# zero here while the client answered #NUM!, and NPER at a rate of -1 answered a
# finite 0 there while this engine answered #NUM!.
#
# TODAY and NOW read the clock, so the two engines cannot be compared on them.
_FAMILY_ALPHABET = ["0", "1", "2", "0.5", "-1", '"2"', "TRUE", "Z1"]
_FAMILIES = (
    ("engineering", engine_core._ENGINEERING_FUNCTIONS),
    ("financial", engine_core._FINANCIAL_FUNCTIONS),
    ("statistical", engine_core._ADOPTED_STATISTICAL_FUNCTIONS),
    ("distribution", engine_core._DISTRIBUTION_FUNCTIONS),
    ("combinatorics", engine_core._COMBINATORICS_FUNCTIONS),
    ("complex", engine_core._COMPLEX_FUNCTIONS),
    ("single", engine_core._ADOPTED_SINGLE_FUNCTIONS - {"TODAY", "NOW"}),
)

# Every function the matrix names is now implemented on both sides, so no
# group is skipped. Keep the pin: losing a function must fail here rather
# than quietly shrinking the matrix.
_EXPECTED_MISSING: tuple = ()


def _groups() -> "OrderedDict[str, List[str]]":
    """The matrix, grouped by operator or function.

    Grouping is what lets one call decide whether a function is worth
    generating: the engine is a subset, so a name it does not implement would
    answer #NAME? on every call and bury the real divergences.
    """
    groups: "OrderedDict[str, List[str]]" = OrderedDict()
    for operator in _OPERATORS:
        groups[f"compare {operator}"] = [
            f"{left}{operator}{right}" for left in _OPERANDS for right in _OPERANDS
        ]
    for operator in _ARITHMETIC:
        groups[f"arithmetic {operator}"] = [
            f"{left}{operator}{right}" for left in _NUMBERS for right in _NUMBERS
        ]
    # Unary signs nest (``--1`` is ``-(-1)``), so cross each operand with sign
    # strings of one to three characters rather than only the single signs;
    # chained signs used to parse as a binary operator with no left operand on
    # the client, which surfaced as #VALUE!.
    sign_strings = [
        "".join(sign) for length in (1, 2, 3) for sign in _product("-+", length)
    ]
    groups["unary sign"] = [
        f"{sign}{number}" for sign in sign_strings for number in _NUMBERS
    ] + ["-(1)", "+A1"]
    groups["percent"] = [f"{number}%" for number in _NUMBERS] + ["1%+1", "10%*2"]
    for name, template in _TEXT_CALLS:
        groups[f"text {name}"] = [
            template.format(x=operand) for operand in _TEXT_OPERANDS
        ]
    for name, template in _DATE_CALLS:
        groups[f"date {name}"] = [
            template.format(x=operand) for operand in _DATE_OPERANDS
        ]
    groups["date arithmetic"] = list(_DATE_EXTRAS)
    for name, template in _CRITERIA_CALLS:
        groups[f"criteria {name}"] = [
            template.format(r=criteria_range, c=criteria, s=sum_range)
            for criteria in _CRITERIA
            for criteria_range, sum_range in _CRITERIA_PAIRS
        ]
    for name, template in _LOOKUP_CALLS:
        groups[f"lookup {name}"] = [
            template.format(k=key, r=lookup_range)
            for key in _LOOKUP_KEYS
            for lookup_range in _LOOKUP_RANGES
        ]
    for name, template in _RANGE_CALLS:
        groups[f"reference {name}"] = [template.format(k=key) for key in _RANGE_KEYS]
    for name, template in _STRUCTURED_CALLS:
        groups[f"structured {name}"] = [
            template.format(r=lookup_range) for lookup_range in _STRUCTURED_RANGES
        ]
    for name, template in _BRANCHING_CALLS:
        groups[f"branching {name}"] = [template.format(k=key) for key in _RANGE_KEYS]
    # PERCENTRANK rounds to `significance` digits. Excel rejects a significance
    # below one, and a rounding factor past 1e308 is not representable, so a
    # larger one returns the unrounded rank. The extremes used to raise an
    # OverflowError on this side and divide an infinity by itself on the
    # client's, which the sheet read as a blank.
    groups["percentrank significance"] = [
        f"PERCENTRANK(A1:A3,1,{significance})"
        for significance in (
            "0",
            "0.5",
            "1",
            "2",
            "3",
            "15",
            "16",
            "308",
            "309",
            "1000",
        )
    ]
    # Positions inside astral text are UTF-16 units on both sides now: a start
    # or a count can land inside a surrogate pair and keep the surviving half,
    # as Excel does. The operand sweep above covers the whole-function forms;
    # these cover the calls whose positions the templates cannot express.
    groups["text astral positions"] = [
        'FIND("😄","😀😃😄",2)',
        'SEARCH("😄","😀😃😄",2)',
        'REPLACE("😀😃😄",2,2,"z")',
        'FIND("","abc",5)',
        'SEARCH("","abc",5)',
    ]
    # AND/OR evaluate every argument: Excel does not short-circuit them, so an
    # error anywhere propagates even when an earlier argument already decides
    # the result, and the first error in argument order wins. The client used
    # to stop at the deciding argument, answering FALSE for AND(FALSE, 1/0).
    groups["branching AND/OR errors"] = [
        "AND(FALSE,1/0)",
        "OR(TRUE,1/0)",
        "AND(FALSE,NA())",
        "OR(TRUE,NA())",
        "AND(NA(),1/0)",
        "AND(1/0,NA())",
        "OR(NA(),1/0)",
        "OR(1/0,NA())",
        "AND(TRUE,TRUE,1/0)",
        "OR(FALSE,FALSE,NA())",
    ]
    # REPT is bounded at 32,767 UTF-16 units on both sides; the server cap
    # used to have no client counterpart, so a huge repeat answered #VALUE!
    # here and a multi-megabyte string there.
    groups["text REPT bound"] = [
        'REPT("x",32767)',
        'REPT("x",32768)',
        'REPT("😀",16384)',
    ]
    # The complex family reads a coefficient out of a double, and an argument
    # past what a double holds raised an OverflowError out of this engine
    # (IMSINH(1e300)) where the client printed text like "Infinity-NaNi". Both
    # answer #NUM! now -- a coefficient a double cannot hold is not a complex
    # value -- and this battery holds them together on the edges the operand
    # alphabet never reaches.
    groups["complex extremes"] = [
        "IMSINH(1e300)",
        "IMSINH(-1e300)",
        'IMSINH("1e300")',
        "IMCOSH(1000)",
        "IMEXP(710)",
        "IMEXP(1e300)",
        "IMCSCH(1e300)",
        "IMSECH(1e300)",
        "IMCOTH(1e300)",
        'IMTAN("1e300i")',
        'IMCSC("1e300i")',
        "IMPRODUCT(1e300,1e300)",
        "IMSUM(1e300,1e300)",
        "IMSQRT(1e300)",
        "IMEXP(709)",
        'IMSINH("1e300i")',
    ]
    # A formula is untrusted input, so a numeric argument must not be able to
    # spin a loop once per unit of itself. These are the arguments that used to
    # hang the engines: DB's depreciation periods, BINOMDIST's summed successes
    # and POISSON's summed events walked one step per unit of an unrestricted
    # double, and the inverse beta's factor overflowed a double mid-bisection.
    # Each loop is now bounded and answers #NUM! past the bound, so a regression
    # fails here (a hang trips the file's timeout rather than passing quietly).
    groups["extreme arguments"] = [
        "DB(1000,100,10,1000000000)",
        "DB(1000,100,10,1e18)",
        "DB(1000,100,1000000000,5)",
        "DB(1000,100,1000000000,1000000000)",
        "DB(1000,1001,10,1e9)",
        "DB(1000,100,1000000000,100000)",
        "BINOMDIST(1000000000,1000000000,0.5,TRUE)",
        "BINOMDIST(170,1e18,0.5,TRUE)",
        "BINOMDIST(100000,100000,0.5,TRUE)",
        "POISSON(1000000000,1,TRUE)",
        "POISSON(1,1000000000,TRUE)",
        "POISSON(100000,1,TRUE)",
        "GAMMAINV(0.5,1e18,1e18)",
        "GAMMAINV(0.5,1e300,1e300)",
        "BETAINV(0.5,1e300,1e300)",
        "BETAINV(0.5,1e18,1e18)",
        "FACTDOUBLE(1000000000)",
        "FACT(1000000000)",
        "COMBIN(1000000000,500000000)",
        "PERMUT(1000000000,500000000)",
        "MULTINOMIAL(1000000000,1000000000)",
        "COMBINA(1000000000,1000000000)",
    ]
    for family, names in _FAMILIES:
        # One group per function, so _missing() can drop just that function.
        for name in sorted(names):
            groups[f"{family} {name}"] = [
                f"{name}({','.join([operand] * arity)})"
                for arity in (1, 2, 3)
                for operand in _FAMILY_ALPHABET
            ]
    return groups


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
        # A NaN or an infinity is not a spreadsheet value; it gets its own kind
        # so the report names it instead of comparing it as a number (and so
        # the client probe, whose results arrive as JSON, can carry it at all).
        number = float(value)
        if not math.isfinite(number):
            if math.isnan(number):
                spelling = "NaN"
            else:
                spelling = "Infinity" if number > 0 else "-Infinity"
            return {"kind": "nonfinite", "value": spelling}
        return {"kind": "number", "value": number}
    if value is None:
        return {"kind": "blank"}
    if isinstance(value, str) and value.startswith("#"):
        return {"kind": "error", "value": value}
    return {"kind": "text", "value": value}


def _evaluate(formula: str) -> Dict[str, Any]:
    """Evaluate one formula in this engine."""
    return _typed(
        FormulaEvaluator(_resolver).evaluate_cell(TABLE["sheet"], 60, 60, formula)
    )


def _missing() -> Dict[str, str]:
    """Groups this engine does not implement, with the call that shows it.

    The engine is a deliberate subset of the client's, so a function it does not
    implement would answer #NAME? to every call and bury the real divergences.
    The group's first call is the probe: an unknown *name* is what produces
    #NAME?, where a wrong argument or a bad value produces something else.
    """
    return {
        name: calls[0]
        for name, calls in _groups().items()
        if (lambda result: result["kind"] == "error" and result["value"] == "#NAME?")(
            _evaluate(calls[0])
        )
    }


def _formulas() -> List[str]:
    missing = _missing()
    return [
        formula
        for name, calls in _groups().items()
        if name not in missing
        for formula in calls
    ]


def _same(ours: Dict[str, Any], theirs: Dict[str, Any]) -> bool:
    if ours["kind"] != theirs["kind"]:
        return False
    if ours["kind"] == "number":
        left, right = ours.get("value"), theirs.get("value")
        # A result JSON cannot carry (a NaN the client probe stringified) is a
        # disagreement, not a crash: the comparison used to subtract None.
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            return False
        return abs(left - right) < 1e-9
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
    ours = {formula: _evaluate(formula) for formula in formulas}
    theirs = _client_results(node, engine, formulas)
    divergences = [
        (formula, ours[formula], theirs[index])
        for index, formula in enumerate(formulas)
        if not _same(ours[formula], theirs[index])
    ]
    assert not divergences, _report(divergences, len(formulas))


def test_the_groups_this_engine_leaves_out_are_the_known_subset():
    """Keep the matrix honest: a newly missing function fails here.

    Every entry below is a function or operator the client engine has and this
    one deliberately does not, so the differential matrix above cannot cover it.
    """
    assert set(_missing()) == set(_EXPECTED_MISSING)


# The functions the client engine implements and this one deliberately does not
# (the README's divergence table): the dynamic-array family, the LINEST-family
# regression machinery, the volatile trio, the introspective forms and the
# array-plumbing wrappers. The matrix cannot cover them -- they answer #NAME? to
# every call, which is exactly what ``_missing`` drops -- so they are pinned by
# name here. Adopting one of them is a deliberate change that has to remove it
# from this list; the client test pins the other half of the row.
_CLIENT_ONLY_FUNCTIONS = (
    "SORT",
    "FILTER",
    "HSTACK",
    "VSTACK",
    "TAKE",
    "DROP",
    "WRAPROWS",
    "WRAPCOLS",
    "TOROW",
    "TOCOL",
    "CHOOSEROWS",
    "CHOOSECOLS",
    "EXPAND",
    "SEQUENCE",
    "UNIQUE",
    "LINEST",
    "LOGEST",
    "TREND",
    "GROWTH",
    "RAND",
    "RANDBETWEEN",
    "RANDARRAY",
    "ISREF",
    "ISFORMULA",
    "SHEET",
    "SHEETS",
    "INFO",
    "ISOMITTED",
    "LAMBDA",
    "LET",
    "ARRAYFORMULA",
    "ANDARRAY",
    "ORARRAY",
)


@pytest.mark.parametrize("name", _CLIENT_ONLY_FUNCTIONS)
def test_the_client_only_functions_answer_name_error(name):
    """The other half of the divergence row: this engine answers #NAME?.

    The client engine implements these and this one does not (see the README's
    divergence table); if one is adopted here, the client's own pin has to be
    updated too, so the row cannot drift.
    """
    assert _evaluate(f"{name}(1)") == {"kind": "error", "value": "#NAME?"}, (
        f"{name} is a client-only function; this engine answered something "
        "other than #NAME?"
    )


def test_the_engines_agree_on_every_shared_case():
    """Every pinned case, compared engine-to-engine rather than to its value.

    Each repository's parity test checks its own engine against the table's
    ``expected`` column, so the two engines meet only through that column -- and
    a comparison loose enough to accept the wrong *kind* of value (Python's
    ``True == 1``, a blank against the 0 it coerces to) passes on one side while
    the other answers something else. This runs the whole table through both
    engines and compares the results with each other, so the column's wording
    cannot hide a disagreement between them.
    """
    node = shutil.which("node")
    engine = _client_engine()
    if node is None or engine is None:
        pytest.skip(
            "cross-engine check needs node and the plexichat-client checkout; "
            "set PLEXICHAT_CLIENT_PATH when the two are not siblings"
        )

    formulas = [case["formula"] for case in TABLE["cases"]]
    ours = {formula: _evaluate(formula) for formula in formulas}
    theirs = _client_results(node, engine, formulas)
    divergences = [
        (formula, ours[formula], theirs[index])
        for index, formula in enumerate(formulas)
        if not _same(ours[formula], theirs[index])
    ]
    assert not divergences, _report(divergences, len(formulas))
