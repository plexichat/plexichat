"""Formula evaluator mixin: evaluation core and standard functions."""

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from ._formula_protocol import FormulaEvaluatorProtocol
from ._formula_shared import (
    CYCLE_ERROR,
    DIV_ZERO,
    ERROR_CODES,
    MAX_CALL_DEPTH,
    MAX_DEPENDENCIES,
    N_A_ERROR,
    NAME_ERROR,
    NUM_ERROR,
    REF_ERROR,
    VALUE_ERROR,
    CellResolver,
    _coerce_number_text,
    _computed_range_error,
    _criteria_operator,
    _fold_case,
    _is_pattern,
    _numeric,
    _to_number,
    _to_number_or_zero,
    _to_text,
    _text_equals,
    _utf16_len,
    _utf16_slice,
    _wildcard_match,
    is_error,
    parse_cell_ref,
    parse_formula,
)


class _DependencyBudgetExceeded(Exception):
    """The per-formula dependency budget ran out (a runaway cycle).

    Raised instead of the formula errors ``_call`` turns into values, so the
    ``_call`` wrapper below never swallows it: the abort has to unwind the
    whole formula rather than become a #NUM! an enclosing error-tolerant
    function could then skip.
    """


# Functions whose arguments are flattened into a ``values`` list by ``_call``.
# Kept beside the dispatch below so the two stay in step: an unknown name must
# still report #NAME? rather than surface an error that happened to sit in its
# arguments.
# The pooled aggregates decide their own error precedence (see _aggregate),
# so the blanket value-function propagation below must not pre-empt them.
_AGGREGATE_ERROR_FUNCTIONS = frozenset(
    # SUMPRODUCT decides its own precedence too: a shape mismatch is #VALUE!
    # before any error cell in the arrays is read.
    {"SUM", "AVERAGE", "MIN", "MAX", "PRODUCT", "SUMPRODUCT"}
)

_VALUE_FUNCTIONS = frozenset(
    {
        "SUM",
        "AVERAGE",
        "COUNT",
        "COUNTA",
        "MIN",
        "MAX",
        "PRODUCT",
        "ABS",
        "SQRT",
        "INT",
        "LEN",
        "UPPER",
        "LOWER",
        "TRIM",
        "LEFT",
        "RIGHT",
        "MID",
        "ROUND",
        "ROUNDUP",
        "ROUNDDOWN",
        "MOD",
        "POWER",
        "CEILING",
        "FLOOR",
        "CONCAT",
        "CONCATENATE",
        "EXACT",
        "PROPER",
        "SUBSTITUTE",
        "DATE",
        "YEAR",
        "MONTH",
        "DAY",
        "DAYS",
        "INDEX",
        "MATCH",
        "INDIRECT",
        "OFFSET",
        "LOOKUP",
        "COUNTBLANK",
        "SUMPRODUCT",
        "TEXT",
        "VALUE",
        "NUMBERVALUE",
        "FIND",
        "SEARCH",
        "REPLACE",
        "REPT",
        "CHAR",
        "CODE",
        "EOMONTH",
        "EDATE",
        "WEEKDAY",
        "WEEKNUM",
        "NETWORKDAYS",
        "WORKDAY",
        # PI is deliberately absent: it ignores its arguments on both engines,
        # so a range -- or an error cell inside one -- must not decide its
        # answer. The propagation loop below would have returned the error
        # before _advanced_trig could ignore it.
        "RADIANS",
        "DEGREES",
        "SIN",
        "COS",
        "TAN",
        "ASIN",
        "ACOS",
        "ATAN",
        "AVERAGEIFS",
        "SUMIFS",
        "COUNTIFS",
        # Adopted from the client engine (see the README's "adopt next" list).
        # Every one of these propagates an error operand; the IS*/ERROR.TYPE
        # additions classify it instead and are handled separately.
        "SIGN",
        "GCD",
        "LCM",
        "SUMSQ",
        "ATAN2",
        "SINH",
        "COSH",
        "TANH",
        "HOUR",
        "MINUTE",
        "SECOND",
        "TIME",
        "DATEVALUE",
        "TODAY",
        "NOW",
        "CLEAN",
        "T",
        "N",
        "ISEVEN",
        "ISODD",
        # The engineering family pools its scalar arguments the same way, so
        # it joins this set for flattening and error-operand propagation; the
        # arity and coercion rules live in ``_engineering``.
        "BIN2DEC",
        "BIN2HEX",
        "BIN2OCT",
        "DEC2BIN",
        "DEC2HEX",
        "DEC2OCT",
        "HEX2BIN",
        "HEX2DEC",
        "HEX2OCT",
        "OCT2BIN",
        "OCT2DEC",
        "OCT2HEX",
        "BITAND",
        "BITOR",
        "BITXOR",
        "BITLSHIFT",
        "BITRSHIFT",
        "DELTA",
        "GESTEP",
        "ERF",
        "ERFC",
        "BESSELJ",
        "BESSELY",
        "CONVERT",
    }
)

# Short-circuiting calls: only the branch that is taken is evaluated.
_BRANCHING_FUNCTIONS = frozenset({"CHOOSE", "IFS", "SWITCH"})

# Functions whose arguments cannot be pooled into one value list, because the
# second argument is a rank, a percentile or a parallel array rather than more
# data to pool.
_STRUCTURED_FUNCTIONS = frozenset(
    {
        "LARGE",
        "SMALL",
        "RANK",
        "PERCENTILE",
        "QUARTILE",
        "SUMX2MY2",
        "SUMX2PY2",
        "SUMXY",
        "TRIMMEAN",
        "STANDARDIZE",
        "PERCENTRANK",
        "CORREL",
        "RSQ",
        "SLOPE",
        "INTERCEPT",
        "FORECAST",
        "STEYX",
    }
)

# Functions that pool every argument but decide for themselves how to treat an
# error operand: one written into the formula propagates, one stored in a
# referenced cell is skipped like text.
_ADOPTED_STATISTICAL_FUNCTIONS = frozenset(
    {
        "MEDIAN",
        "MODE",
        "STDEV",
        "VAR",
        "AVERAGEA",
        "MAXA",
        "MINA",
        "AVEDEV",
        "DEVSQ",
        "GEOMEAN",
        "HARMEAN",
        "VARP",
        "STDEVP",
    }
)

# The client engine's FLAT_RANGE_FUNCTIONS, LAZY_FUNCTIONS and
# ERROR_TOLERANT_FUNCTIONS: the calls whose arguments are *not* evaluated
# eagerly. A pooled range reader (SUM, MEDIAN, CORREL), a short-circuiting
# branch (IF, CHOOSE), an error-classifying inspector (IS*) and the lazy
# lookups (XLOOKUP, OFFSET) read their arguments for themselves, so an argument
# that is an error must not be propagated before the body. Every other
# function lets an error *argument* pass in argument order (see
# _argument_error).
_EAGER_ARGUMENT_ERROR_EXCLUDED = frozenset(
    {
        # Flat range readers (client FLAT_RANGE_FUNCTIONS).
        "SUM",
        "AVERAGE",
        "COUNT",
        "COUNTA",
        "MIN",
        "MAX",
        "PRODUCT",
        "COUNTBLANK",
        "SUMPRODUCT",
        "SUMIF",
        "COUNTIF",
        "AVERAGEIF",
        "SUMIFS",
        "COUNTIFS",
        "AVERAGEIFS",
        "AVERAGEA",
        "MAXA",
        "MINA",
        "MEDIAN",
        "MODE",
        "LARGE",
        "SMALL",
        "RANK",
        "PERCENTILE",
        "QUARTILE",
        "GEOMEAN",
        "HARMEAN",
        "AVEDEV",
        "DEVSQ",
        "STDEV",
        "STDEVP",
        "VAR",
        "VARP",
        "TRIMMEAN",
        "PERCENTRANK",
        "CORREL",
        "RSQ",
        "SLOPE",
        "INTERCEPT",
        "STEYX",
        "FORECAST",
        "FREQUENCY",
        # Short-circuiting and error-classifying calls (client LAZY_FUNCTIONS).
        "IF",
        "IFERROR",
        "IFNA",
        "AND",
        "OR",
        "IFS",
        "SWITCH",
        "CHOOSE",
        "ISREF",
        "ISFORMULA",
        "SHEET",
        "SHEETS",
        "XLOOKUP",
        "OFFSET",
        "ISERROR",
        "ISNA",
        "ISBLANK",
        "ISNUMBER",
        "ISTEXT",
        "ISLOGICAL",
        "ISNONTEXT",
        "TYPE",
        "ERROR.TYPE",
        "PI",
    }
)

# Flat range readers -- the client engine's FLAT_RANGE_FUNCTIONS -- that are
# *not* already excluded from the eager argument-error rule. Each defers every
# reference argument (a single cell as much as a range) and reads the cells
# itself, but still evaluates its scalar arguments eagerly: an error among them
# propagates in argument order before the body runs. These bodies decide their
# own range-error precedence, so the flat rule is applied to the scalar
# arguments here rather than pooling the call.
_FLAT_ARGUMENT_ERROR_FUNCTIONS = frozenset(
    {
        "RANK",
        "PERCENTRANK",
        "FORECAST",
        "SUMPRODUCT",
    }
)

# The client engine's financial, distribution and combinatorics families,
# adopted wholesale. Like _STRUCTURED_FUNCTIONS they read their arguments as
# separate slots -- a rate, a period, a parallel range -- rather than one
# pooled value list, so they take the raw argument nodes.
_FINANCIAL_FUNCTIONS = frozenset(
    {
        "PMT",
        "FV",
        "PV",
        "NPV",
        "NPER",
        "RATE",
        "IPMT",
        "PPMT",
        "IRR",
        "MIRR",
        "XNPV",
        "SLN",
        "SYD",
        "DB",
    }
)

_DISTRIBUTION_FUNCTIONS = frozenset(
    {
        "NORMDIST",
        "NORMSDIST",
        "NORMINV",
        "NORMSINV",
        "NORMINVS",
        "BINOMDIST",
        "POISSON",
        "EXPONDIST",
        "WEIBULL",
        "GAMMALN",
        "GAMMADIST",
        "GAMMAINV",
        "BETADIST",
        "BETAINV",
        "NEGBINOMDIST",
        "FREQUENCY",
    }
)

_COMBINATORICS_FUNCTIONS = frozenset(
    {"COMBIN", "COMBINA", "PERMUT", "FACT", "FACTDOUBLE", "MULTINOMIAL"}
)

# The client engine's engineering family, adopted wholesale: the base
# conversions (BIN2DEC through OCT2HEX), the bitwise operations, DELTA/GESTEP,
# ERF/ERFC, the Bessel functions and CONVERT. Like _ADOPTED_SINGLE_FUNCTIONS
# they pool their arguments into one value list (every parameter is a scalar)
# and decide arity for themselves.
_ENGINEERING_FUNCTIONS = frozenset(
    {
        "BIN2DEC",
        "BIN2HEX",
        "BIN2OCT",
        "DEC2BIN",
        "DEC2HEX",
        "DEC2OCT",
        "HEX2BIN",
        "HEX2DEC",
        "HEX2OCT",
        "OCT2BIN",
        "OCT2DEC",
        "OCT2HEX",
        "BITAND",
        "BITOR",
        "BITXOR",
        "BITLSHIFT",
        "BITRSHIFT",
        "DELTA",
        "GESTEP",
        "ERF",
        "ERFC",
        "BESSELJ",
        "BESSELY",
        "CONVERT",
    }
)

# The adopted functions that take a single scalar, or no argument at all.
_ADOPTED_SINGLE_FUNCTIONS = frozenset(
    {
        "SIGN",
        "GCD",
        "LCM",
        "SUMSQ",
        "ATAN2",
        "SINH",
        "COSH",
        "TANH",
        "HOUR",
        "MINUTE",
        "SECOND",
        "TIME",
        "DATEVALUE",
        "TODAY",
        "NOW",
        "CLEAN",
        "T",
        "N",
        "ISEVEN",
        "ISODD",
        "ISLOGICAL",
        "ISNONTEXT",
        "ERROR.TYPE",
    }
)

# The client engine's complex-number family, adopted wholesale. These pool
# their arguments like the other value functions and read each one as Excel's
# complex-number text.
_COMPLEX_FUNCTIONS = frozenset(
    {
        "COMPLEX",
        "IMABS",
        "IMREAL",
        "IMAGINARY",
        "IMARGUMENT",
        "IMCONJUGATE",
        "IMSUM",
        "IMSUB",
        "IMPRODUCT",
        "IMDIV",
        "IMMOD",
        "IMEXP",
        "IMLOG",
        "IMSQRT",
        "IMCOS",
        "IMSIN",
        "IMTAN",
        "IMCSC",
        "IMSEC",
        "IMCOT",
        "IMCOSH",
        "IMSINH",
        "IMTANH",
        "IMCSCH",
        "IMSECH",
        "IMCOTH",
    }
)

# The families whose arguments are all single scalars: the engineering set and
# the complex-number set. Their bodies read ``values[k]`` slot by slot, so the
# dispatcher hands them one entry per argument (see the slot branch in
# ``_call``) rather than the flattened cell list a pooled reader wants.
_SCALAR_SLOT_FUNCTIONS = (
    _ENGINEERING_FUNCTIONS
    | _COMPLEX_FUNCTIONS
    | frozenset(
        {
            "ATAN2",
            "DAYS",
            "EDATE",
            "EOMONTH",
            "WEEKDAY",
            "WEEKNUM",
            "WORKDAY",
            "NETWORKDAYS",
            # MID and the date/time constructors coerce their slots in order too
            # (MID reads start before length, DATE reads year before month
            # before day, TIME reads every component in turn), so a coercion
            # failure in an earlier slot beats an error cell in a later range.
            "MID",
            "DATE",
            "TIME",
        }
    )
)

# A scalar family whose tail is a range: NETWORKDAYS and WORKDAY read their two
# required arguments as scalars and the holiday list as a range, so only the
# leading slots are scalarized and the rest are pooled.
_SCALAR_SLOT_LEAD = {"NETWORKDAYS": 2, "WORKDAY": 2}

# Functions that classify an error operand instead of propagating it. COUNT
# counts numbers only and COUNTA counts anything non-empty (an error is data);
# the *IFS family matches an error cell as "no match" rather than failing the
# whole formula. COUNTBLANK tolerates an error read out of a cell (an error
# cell is not blank) but propagates one written into the formula, so the
# propagation loop in ``_call`` decides it by provenance.
_ERROR_TOLERANT_FUNCTIONS = frozenset(
    {"COUNT", "COUNTA", "SUMIFS", "COUNTIFS", "AVERAGEIFS"}
)

# The arity of every fixed-arity function, mirrored from the client engine's
# ``arity.js``. A stray argument is #VALUE! *before* the arguments are
# evaluated, so a function called with the wrong number of them never lets an
# error operand decide the answer -- NOT(1/0, 1/0) is #VALUE!, not #DIV/0!.
# Each entry is ``(min, max, step)``: valid when ``min <= count <= max`` and,
# when *step* is not None, ``count % 2 == step``. ``max`` of None is unbounded.
# A name absent from the table is variadic (SUM, CONCAT, ...) or checks its own
# bounds (PI ignores its arguments; IF/IFS/SWITCH/CHOOSE read the raw nodes),
# so the sweep never turns a #NAME? into #VALUE!. The client-only names (the
# introspective forms, the regression machinery and the array family) are
# absent: this engine answers #NAME? for them.
_ARITY: Dict[str, Tuple[int, Optional[int], Optional[int]]] = {
    "ABS": (1, 1, None),
    "ACOS": (1, 1, None),
    "AND": (1, None, None),
    "ASIN": (1, 1, None),
    "ATAN": (1, 1, None),
    "ATAN2": (2, 2, None),
    "AVERAGEIF": (2, 3, None),
    "AVERAGEIFS": (3, None, 1),
    "BESSELJ": (2, 2, None),
    "BESSELY": (2, 2, None),
    "BETADIST": (3, 5, None),
    "BETAINV": (3, 5, None),
    "BIN2DEC": (1, 2, None),
    "BIN2HEX": (1, 2, None),
    "BIN2OCT": (1, 2, None),
    "BINOMDIST": (4, 4, None),
    "BITAND": (2, 2, None),
    "BITLSHIFT": (2, 2, None),
    "BITOR": (2, 2, None),
    "BITRSHIFT": (2, 2, None),
    "BITXOR": (2, 2, None),
    "CEILING": (2, 2, None),
    "CHAR": (1, 1, None),
    "CLEAN": (1, 1, None),
    "CODE": (1, 1, None),
    "COMBIN": (2, 2, None),
    "COMBINA": (2, 2, None),
    # Variadic text joins. They live here for the eager argument-error rule:
    # CONCAT(A1:A5, NA()) is #N/A, the error operand, ahead of the range's
    # error cell -- the client evaluates every non-deferred argument first.
    "CONCAT": (0, None, None),
    "CONCATENATE": (0, None, None),
    "COMPLEX": (1, 3, None),
    "CONVERT": (3, 3, None),
    "CORREL": (2, 2, None),
    "COS": (1, 1, None),
    "COSH": (1, 1, None),
    "COUNTBLANK": (1, 1, None),
    "COUNTIF": (2, 2, None),
    "COUNTIFS": (2, None, 0),
    "DATE": (3, 3, None),
    "DATEVALUE": (1, 1, None),
    "DAY": (1, 1, None),
    "DAYS": (2, 2, None),
    "DB": (4, 5, None),
    "DEC2BIN": (1, 2, None),
    "DEC2HEX": (1, 2, None),
    "DEC2OCT": (1, 2, None),
    "DEGREES": (1, 1, None),
    "DELTA": (1, 2, None),
    "EDATE": (2, 2, None),
    "EOMONTH": (2, 2, None),
    "ERF": (1, 2, None),
    "ERFC": (1, 1, None),
    "EXACT": (2, 2, None),
    "EXPONDIST": (3, 3, None),
    "FACT": (1, 1, None),
    "FACTDOUBLE": (1, 1, None),
    "FALSE": (0, 0, None),
    "FIND": (2, 3, None),
    "FLOOR": (2, 2, None),
    "FORECAST": (3, 3, None),
    "FREQUENCY": (2, 2, None),
    "FV": (3, 5, None),
    "GAMMADIST": (4, 4, None),
    "GAMMAINV": (3, 3, None),
    "GAMMALN": (1, 1, None),
    "GCD": (1, None, None),
    "GESTEP": (1, 2, None),
    "HEX2BIN": (1, 2, None),
    "HEX2DEC": (1, 2, None),
    "HEX2OCT": (1, 2, None),
    "HLOOKUP": (3, 4, None),
    "HOUR": (1, 1, None),
    "IMABS": (1, 1, None),
    "IMAGINARY": (1, 1, None),
    "IMARGUMENT": (1, 1, None),
    "IMCONJUGATE": (1, 1, None),
    "IMCOS": (1, 1, None),
    "IMCOSH": (1, 1, None),
    "IMCOT": (1, 1, None),
    "IMCOTH": (1, 1, None),
    "IMCSC": (1, 1, None),
    "IMCSCH": (1, 1, None),
    "IMDIV": (2, 2, None),
    "IMEXP": (1, 1, None),
    "IMLOG": (1, 1, None),
    "IMMOD": (2, 2, None),
    "IMPRODUCT": (1, None, None),
    "IMREAL": (1, 1, None),
    "IMSEC": (1, 1, None),
    "IMSECH": (1, 1, None),
    "IMSIN": (1, 1, None),
    "IMSINH": (1, 1, None),
    "IMSQRT": (1, 1, None),
    "IMSUB": (2, 2, None),
    "IMSUM": (1, None, None),
    "IMTAN": (1, 1, None),
    "IMTANH": (1, 1, None),
    "INDEX": (2, 3, None),
    "INDIRECT": (1, 2, None),
    "INT": (1, 1, None),
    "INTERCEPT": (2, 2, None),
    "IPMT": (4, 6, None),
    "IRR": (1, 2, None),
    "ISEVEN": (1, 1, None),
    "ISODD": (1, 1, None),
    "LARGE": (2, 2, None),
    "LCM": (1, None, None),
    "LEFT": (1, 2, None),
    "LEN": (1, 1, None),
    "LOOKUP": (2, 3, None),
    "LOWER": (1, 1, None),
    "MATCH": (2, 3, None),
    "MID": (3, 3, None),
    "MINUTE": (1, 1, None),
    "MIRR": (3, 3, None),
    "MOD": (2, 2, None),
    "MONTH": (1, 1, None),
    # Any number of arguments, including none: MULTINOMIAL() is 1 (the empty
    # product over 0!). It lives here for its arity bound *and* so the eager
    # argument-error rule covers it -- MULTINOMIAL("x", 1/0) is #DIV/0!, the
    # error operand, rather than the #VALUE! the first slot's coercion earns.
    "MULTINOMIAL": (0, None, None),
    "N": (1, 1, None),
    "NA": (0, 0, None),
    "NEGBINOMDIST": (3, 3, None),
    "NETWORKDAYS": (2, 3, None),
    "NORMDIST": (4, 4, None),
    "NORMINV": (3, 3, None),
    "NORMINVS": (1, 1, None),
    "NORMSDIST": (1, 1, None),
    "NORMSINV": (1, 1, None),
    "NOW": (0, 0, None),
    "NOT": (1, 1, None),
    "NPER": (3, 5, None),
    "NPV": (2, None, None),
    "NUMBERVALUE": (1, 3, None),
    "OCT2BIN": (1, 2, None),
    "OCT2DEC": (1, 2, None),
    "OCT2HEX": (1, 2, None),
    "OFFSET": (3, 5, None),
    "OR": (1, None, None),
    "PERCENTILE": (2, 2, None),
    "PERCENTRANK": (2, 3, None),
    "PERMUT": (2, 2, None),
    "PI": (0, None, None),
    "PMT": (3, 5, None),
    "POISSON": (3, 3, None),
    "POWER": (2, 2, None),
    "PPMT": (4, 6, None),
    "PROPER": (1, 1, None),
    "PV": (3, 5, None),
    "QUARTILE": (2, 2, None),
    "RADIANS": (1, 1, None),
    "RANK": (2, 3, None),
    "RATE": (3, 6, None),
    "REPLACE": (4, 4, None),
    "REPT": (2, 2, None),
    "RIGHT": (1, 2, None),
    "ROUND": (2, 2, None),
    "ROUNDDOWN": (2, 2, None),
    "ROUNDUP": (2, 2, None),
    "RSQ": (2, 2, None),
    "SEARCH": (2, 3, None),
    "SECOND": (1, 1, None),
    "SIGN": (1, 1, None),
    "SIN": (1, 1, None),
    "SINH": (1, 1, None),
    "SLN": (3, 3, None),
    "SLOPE": (2, 2, None),
    "SMALL": (2, 2, None),
    "SQRT": (1, 1, None),
    "STANDARDIZE": (3, 3, None),
    "STEYX": (2, 2, None),
    "SUBSTITUTE": (3, 4, None),
    "SUMIF": (2, 3, None),
    "SUMIFS": (3, None, 1),
    "SUMPRODUCT": (1, None, None),
    "SUMSQ": (1, None, None),
    "SUMX2MY2": (2, 2, None),
    "SUMX2PY2": (2, 2, None),
    "SUMXY": (2, 2, None),
    "SYD": (4, 4, None),
    "T": (1, 1, None),
    "TAN": (1, 1, None),
    "TANH": (1, 1, None),
    "TEXT": (2, 2, None),
    "TIME": (3, 3, None),
    "TODAY": (0, 0, None),
    "TRIM": (1, 1, None),
    "TRIMMEAN": (2, 2, None),
    "TRUE": (0, 0, None),
    "UPPER": (1, 1, None),
    "VALUE": (1, 1, None),
    "VLOOKUP": (3, 4, None),
    "WEEKDAY": (1, 2, None),
    "WEEKNUM": (1, 2, None),
    "WEIBULL": (4, 4, None),
    "WORKDAY": (2, 3, None),
    "XLOOKUP": (3, 6, None),
    "XNPV": (3, 3, None),
    "YEAR": (1, 1, None),
}


class FormulaEvaluatorCore(FormulaEvaluatorProtocol):
    def __init__(
        self,
        resolver: CellResolver,
        dependencies: int = MAX_DEPENDENCIES,
        named_ranges: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._resolver = resolver
        self._dependencies = dependencies
        self._used = 0
        self._named_ranges = named_ranges or {}
        # Cache key includes the formula text: the same coordinates may be
        # evaluated with a different formula when the evaluator is reused
        # (e.g. ``compute_value`` evaluates arbitrary cells, and a cell's
        # formula may change between reads). Keying on the formula keeps the
        # memoization sound in every reuse scenario.
        self._computed: Dict[Tuple[Optional[str], int, int, str], Any] = {}

    def evaluate_cell(
        self,
        sheet: Optional[str],
        row: int,
        column: int,
        formula: str,
        seen: Optional[set] = None,
        depth: int = 0,
    ) -> Any:
        key = (sheet, row, column, formula)
        if depth == 0:
            # Root evaluation starts a fresh dependency budget. Without this
            # the counter would be cumulative across every formula cell in a
            # viewport, so a dense sheet of many multi-reference formulas
            # could spuriously exceed the bound and return #NUM!.
            self._used = 0
        if depth > MAX_CALL_DEPTH:
            return CYCLE_ERROR
        if seen is None:
            seen = set()
        if key in seen:
            return CYCLE_ERROR
        if key in self._computed:
            return self._computed[key]
        self._used += 1
        if self._used > self._dependencies:
            raise _DependencyBudgetExceeded
        seen.add(key)
        try:
            node = parse_formula(formula)
            if node[0] == "ref" and ":" in node[1]:
                # A bare range is the *matrix* the client engine's top-level
                # ``evaluate`` returns (and a formula cell holding one spills
                # the same way). Reading it through ``_eval`` applied the
                # scalar-slot pooled rule instead, so ``=A1:A5`` was its error
                # cell (#DIV/0!) rather than the five-cell column.
                value = self._range_matrix(node, sheet, seen, depth)
            else:
                value = self._eval(node, sheet, seen, depth)
            self._computed[key] = value
            return value
        except _DependencyBudgetExceeded:
            return NUM_ERROR
        except ValueError as exc:
            message = str(exc)
            return message if message in ERROR_CODES else VALUE_ERROR
        finally:
            seen.discard(key)

    def _resolve(
        self, sheet: Optional[str], row: int, column: int, seen: set, depth: int
    ) -> Any:
        cell = self._resolver(sheet, row, column)
        if cell is None:
            return None
        self._used += 1
        if self._used > self._dependencies:
            raise _DependencyBudgetExceeded
        formula = cell.get("formula")
        if formula:
            return self.evaluate_cell(sheet, row, column, formula, seen, depth + 1)
        return cell.get("value")

    def _range_values(
        self,
        sheet: Optional[str],
        row1: int,
        col1: int,
        row2: int,
        col2: int,
        seen: set,
        depth: int,
    ) -> List[Any]:
        values: List[Any] = []
        r_start, r_end = sorted((row1, row2))
        c_start, c_end = sorted((col1, col2))
        for r in range(r_start, r_end + 1):
            for c in range(c_start, c_end + 1):
                self._used += 1
                if self._used > self._dependencies:
                    raise _DependencyBudgetExceeded
                values.append(self._resolve(sheet, r, c, seen, depth + 1))
        return values

    def _eval(self, node: Any, sheet: Optional[str], seen: set, depth: int) -> Any:
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "str":
            return node[1]
        if kind == "bool":
            return node[1]
        if kind == "blank":
            # An omitted argument is Excel's "missing" operand, which behaves
            # exactly like a blank cell (0 in arithmetic, "" as text).
            return None
        if kind == "ref":
            ref = node[1]
            if ":" in ref:
                # A range reached a slot that wants one value (an operator, an
                # IF condition, a lookup key). It used to fall through
                # ``parse_cell_ref`` and answer #REF! for the colon; the
                # client engine read it as a row-major matrix. Both now apply
                # the pooled rule the scalar functions share: an error cell in
                # the range propagates, a one-cell range is that cell, and
                # anything else is #VALUE! -- never a comma-joined string.
                first, second = ref.split(":", 1)
                try:
                    s1, r1, c1 = parse_cell_ref(first, self._named_ranges)
                    s2, r2, c2 = parse_cell_ref(second, self._named_ranges)
                except ValueError:
                    return REF_ERROR
                values = self._range_values(s1 or sheet, r1, c1, r2, c2, seen, depth)
                for value in values:
                    if is_error(value):
                        return value
                if len(values) == 1:
                    return values[0]
                return VALUE_ERROR
            try:
                r_sheet, row, column = parse_cell_ref(ref, self._named_ranges)
            except ValueError:
                # A name that is neither a cell reference nor a defined named
                # range is #REF!, the answer the client engine gives. It used
                # to reach evaluate_cell's catch-all and surface as #VALUE!.
                return REF_ERROR
            target_sheet = r_sheet or sheet
            return self._resolve(target_sheet, row, column, seen, depth)
        if kind == "neg":
            value = self._eval(node[1], sheet, seen, depth)
            if is_error(value):
                return value
            return -_to_number(value)
        if kind == "%":
            value = self._eval(node[1], sheet, seen, depth)
            if is_error(value):
                return value
            return _to_number(value) / 100.0
        if kind == "cmp":
            # Comparison nodes are ("cmp", op, left, right).
            op = node[1]
            left = self._eval(node[2], sheet, seen, depth)
            right = self._eval(node[3], sheet, seen, depth)
            return self._apply(op, left, right)
        if kind in ("+", "-", "*", "/", "^", "&"):
            op = kind
            left = self._eval(node[1], sheet, seen, depth)
            right = self._eval(node[2], sheet, seen, depth)
            return self._apply(op, left, right)
        if kind == "call":
            name = node[1]
            args = node[2]
            return self._call(name, args, sheet, seen, depth)
        raise ValueError(VALUE_ERROR)

    def _apply(self, op: str, left: Any, right: Any) -> Any:
        if is_error(left):
            return left
        if is_error(right):
            return right
        if op == "&":
            return _to_text(left) + _to_text(right)
        if op in ("=", "<>", "<", ">", "<=", ">="):
            if op in ("=", "<>"):
                if isinstance(left, str) or isinstance(right, str):
                    # Excel's text comparison ignores case; EXACT is the
                    # case-sensitive test. A plain ``==`` here answered FALSE
                    # for "a" = "A".
                    comparison = _text_equals(left, right)
                elif isinstance(left, bool) != isinstance(right, bool):
                    # Excel ranks types before comparing, so a logical is never
                    # its numeric value: TRUE = 1 is FALSE and TRUE > 1 is
                    # TRUE. Left to Python, ``True == 1`` answered TRUE, which
                    # disagreed with the client engine (and with the ranked
                    # comparisons the criteria functions already use).
                    comparison = False
                else:
                    comparison = left == right
                return comparison if op == "=" else not comparison
            try:
                ln = _to_number(left)
                rn = _to_number(right)
            except ValueError as exc:
                return str(exc)
            if op == "<":
                return ln < rn
            if op == ">":
                return ln > rn
            if op == "<=":
                return ln <= rn
            return ln >= rn
        if op == "+":
            # Text that reads as a number is coerced ("2"+3 is 5) and anything
            # else is #VALUE!. The old guard rejected *every* text operand, so a
            # numeric string was refused even though "2"*3 and "2"/2 both
            # coerced it.
            try:
                return _to_number(left) + _to_number(right)
            except ValueError as exc:
                return str(exc)
        if op == "-":
            try:
                return _to_number(left) - _to_number(right)
            except ValueError as exc:
                return str(exc)
        if op == "*":
            try:
                return _to_number(left) * _to_number(right)
            except ValueError as exc:
                return str(exc)
        if op == "/":
            try:
                rn = _to_number(right)
            except ValueError as exc:
                return str(exc)
            if rn == 0:
                return DIV_ZERO
            return _to_number(left) / rn
        if op == "^":
            try:
                base = _to_number(left)
                exponent = _to_number(right)
            except ValueError as exc:
                return str(exc)
            if base == 0 and exponent < 0:
                # 0 ** -n is 1 / 0**n, a division by zero in Excel.
                return DIV_ZERO
            try:
                return math.pow(base, exponent)
            except (ValueError, OverflowError):
                return NUM_ERROR
        return VALUE_ERROR

    def _call(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """Evaluate a function call, yielding its error value when it fails.

        The client engine evaluates a function body to a *value*: a body that
        fails yields the error it raised, and an enclosing function can then
        classify or skip it (COUNT ignores it, ISERROR reports TRUE, PI ignores
        its arguments). Many bodies here raise ``ValueError`` /
        ``ZeroDivisionError`` / ``OverflowError`` instead, and without this
        wrapper the exception unwound straight past every enclosing call, so
        ``COUNT(ABS("x"))`` was #VALUE! (not 0) and ``PI(ABS("x"))`` was
        #VALUE! (not pi). ``_DependencyBudgetExceeded`` is deliberately *not*
        caught: a runaway cycle has to abort the whole formula rather than
        become a #NUM! an enclosing error-tolerant function could skip.
        """
        try:
            return self._call_impl(name, args, sheet, seen, depth)
        except _DependencyBudgetExceeded:
            raise
        except ZeroDivisionError:
            return DIV_ZERO
        except OverflowError:
            return NUM_ERROR
        except ValueError as exc:
            message = str(exc)
            return message if message in ERROR_CODES else VALUE_ERROR

    def _call_impl(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        # A stray argument is #VALUE! before any argument is evaluated, so a
        # function called with the wrong number of them never lets an error
        # operand decide the answer (see _ARITY). An unknown name is not in
        # the table, so this never turns a #NAME? into #VALUE!.
        arity = _ARITY.get(name)
        if arity is not None:
            low, high, step = arity
            if len(args) < low or (high is not None and len(args) > high):
                return VALUE_ERROR
            if step is not None and len(args) % 2 != step:
                return VALUE_ERROR
        # The client engine evaluates every argument before it calls the
        # function, so an error *argument* propagates in argument order --
        # ahead of an error cell in a later range read, a length mismatch or a
        # coercion failure. The pooled range readers and the lazy/classifying
        # calls read their arguments for themselves and are excluded.
        if name in _ARITY and name not in _EAGER_ARGUMENT_ERROR_EXCLUDED:
            error = self._argument_error(args, sheet, seen, depth)
            if error is not None:
                return error
        elif name in _FLAT_ARGUMENT_ERROR_FUNCTIONS:
            # A flat range reader defers *every* reference -- a ``:`` range
            # and a single cell alike -- to read the cells itself, but it
            # still evaluates its scalar arguments eagerly and propagates an
            # error among them in argument order. RANK("x", 1/0) is #DIV/0!
            # here and in the client engine, where reading only the body left
            # the first slot's #VALUE! to win.
            error = self._argument_error(
                args, sheet, seen, depth, defer_references=True
            )
            if error is not None:
                return error
        # Evaluate the arguments lazily where the function is short-circuiting.
        if name == "IF":
            # Excel's third argument is optional: IF(test, value_if_true) is
            # legal and a false test yields FALSE. Requiring exactly three
            # arguments made that form -- and any arithmetic on it -- #VALUE!.
            if len(args) not in (2, 3):
                return VALUE_ERROR
            # The condition is inspected for an error *before* it is coerced to
            # a boolean. ``_truthy`` returns a plain bool, so the old guard
            # could never fire and IF(1/0, 1, 2) took the true branch -- a
            # non-empty error string is truthy -- instead of returning
            # #DIV/0!. The condition and the chosen branch are single values,
            # so a matrix -- whether a range or a nested call -- is read through
            # ``_scalar_or`` the way the client engine's ``scalarize`` reads it
            # (IF(1, FREQUENCY(A1:A5, A1:A5)) is #VALUE!).
            condition = self._scalar_or(args[0], sheet, seen, depth)
            if is_error(condition):
                return condition
            if self._truthy(condition):
                return self._scalar_or(args[1], sheet, seen, depth)
            if len(args) == 3:
                return self._scalar_or(args[2], sheet, seen, depth)
            return False
        if name in ("AND", "OR"):
            if not args:
                return VALUE_ERROR
            # Flattened like a pooled function: Excel's AND/OR accept a range
            # and read every cell, so AND(A1:A3) is TRUE over 1, 2, 3. Reading
            # each argument with ``_eval`` answered #REF! for the range, which
            # made the client engine (which reads the cells) the only one that
            # could use the idiom. An error operand still propagates --
            # AND(1/0) and AND(A1:A5) with an error in A5 are #DIV/0!.
            evaluated: List[Any] = []
            for arg in args:
                expanded, _ = self._expand_arg(arg, sheet, seen, depth)
                evaluated.extend(expanded)
            for value in evaluated:
                if is_error(value):
                    return value
            results = [self._truthy(value) for value in evaluated]
            return all(results) if name == "AND" else any(results)
        if name == "NOT":
            if len(args) != 1:
                return VALUE_ERROR
            value = self._scalar_or(args[0], sheet, seen, depth)
            if is_error(value):
                return value
            return not self._truthy(value)
        if name in ("TRUE", "FALSE"):
            # Excel accepts TRUE()/FALSE() as zero-argument calls alongside
            # the bare literals; the client engine registers them as
            # functions too. The arity is checked before evaluating, the way
            # NOT/IFERROR do, so a stray argument is #VALUE!.
            if args:
                return VALUE_ERROR
            return name == "TRUE"
        if name in ("VLOOKUP", "HLOOKUP"):
            return self._lookup(name, args, sheet, seen, depth)
        if name in ("INDEX", "MATCH", "INDIRECT", "OFFSET", "LOOKUP", "XLOOKUP"):
            # Raw arguments, before the range expansion below: these read
            # their ranges cell by cell, so expanding every range up front
            # propagated an error cell the lookup never touches --
            # INDEX(A1:A5, 1) was #DIV/0! when A5 holds 1/0, where the
            # client engine (and Excel) answer A1. VLOOKUP above is the
            # same shape for the same reason.
            return self._lookup_advanced(name, args, sheet, seen, depth)
        if name in ("SUMIF", "COUNTIF", "AVERAGEIF"):
            return self._criteria(name, args, sheet, seen, depth)
        if name in ("IFERROR", "IFNA"):
            if len(args) != 2:
                return VALUE_ERROR
            # Both slots are single values, so a matrix follows the pooled
            # rule: the error or #VALUE! it raises is what IFERROR catches and
            # IFNA substitutes only for #N/A -- IFERROR(FREQUENCY(A1:A5, A1:A5),
            # 1) is 1, matching the client engine's scalarize.
            error, value = self._scalar_slot(args[0], sheet, seen, depth)
            if error is not None:
                if name == "IFNA" and error != N_A_ERROR:
                    return error
                return self._scalar_or(args[1], sheet, seen, depth)
            return value
        if name in _BRANCHING_FUNCTIONS:
            return self._branched(name, args, sheet, seen, depth)
        if name in _STRUCTURED_FUNCTIONS:
            return self._structured(name, args, sheet, seen, depth)
        if name in (
            "ROUND",
            "ROUNDUP",
            "ROUNDDOWN",
            "MOD",
            "POWER",
            "CEILING",
            "FLOOR",
        ):
            # Scalar slots, read per argument in client order: a coercion
            # failure in an earlier slot beats an error cell in a later
            # range (ROUND(A1:A5, "x") is #VALUE!, not the range's #DIV/0!),
            # which the pooled propagation below could not express -- it
            # scanned the flattened values first and always returned the
            # error cell. ROUND reads its digits first, like the client.
            return self._double_args(name, args, sheet, seen, depth)
        # The numeric families compute in double precision, and the client
        # engine's helpers never raise: an overflow there is an Infinity or a
        # NaN that its result check turns into #NUM!. Python's arithmetic
        # raises instead -- ``math.exp`` overflows, and a denominator whose
        # subtraction rounded away divides by zero -- so the same argument has
        # to land on the same #NUM! here rather than escaping as an exception.
        try:
            if name in _FINANCIAL_FUNCTIONS:
                return self._financial(name, args, sheet, seen, depth)
            if name in _DISTRIBUTION_FUNCTIONS:
                return self._distribution(name, args, sheet, seen, depth)
            if name in _COMBINATORICS_FUNCTIONS:
                return self._combinatorics(name, args, sheet, seen, depth)
        except (OverflowError, ZeroDivisionError, ValueError):
            return NUM_ERROR

        values: List[Any] = []
        # Which entries were read out of a reference, running parallel to
        # ``values``. Excel coerces a text *argument* (SUM("2") is 2) but never
        # a value that came from a reference, so the aggregates need the
        # provenance and not just the flattened values.
        from_reference: List[bool] = []
        if name in _SCALAR_SLOT_FUNCTIONS:
            # A scalar family reads each argument as one slot, the way the
            # client engine's ``scalarize``/``toNumber`` do: a one-cell
            # reference or expression is that value, an error in the slot
            # propagates when the body reads it, and a multi-cell range is
            # #VALUE!. Pooling the range into its cells instead would let an
            # error cell decide the answer before the body read an earlier
            # slot (``BESSELJ("x", A1:A5)`` was the range's #DIV/0! where the
            # client answers the first slot's #VALUE!).
            lead = _SCALAR_SLOT_LEAD.get(name)
            for index, arg in enumerate(args):
                if lead is not None and index >= lead:
                    expanded, is_reference = self._expand_arg(arg, sheet, seen, depth)
                    values.extend(expanded)
                    from_reference.extend([is_reference] * len(expanded))
                    continue
                error, value = self._scalar_slot(arg, sheet, seen, depth)
                values.append(error if error is not None else value)
                from_reference.append(False)
        else:
            for arg in args:
                expanded, is_reference = self._expand_arg(arg, sheet, seen, depth)
                values.extend(expanded)
                from_reference.extend([is_reference] * len(expanded))
        # An error operand propagates out of the value-based functions. Excel
        # treats it as data the caller has to see rather than a value to
        # coerce, and without this the coercion helpers mangled it instead of
        # reporting it: PROPER(1/0) re-cased the error text into "#Div/0!",
        # REPLACE(1/0, 1, 1, "x") concatenated it into "xDIV/0!", and
        # ROUND(1, 1/0) blamed the wrong operand with #VALUE!. COUNT/COUNTA/
        # COUNTBLANK and the *IFS family classify an error themselves (see
        # _ERROR_TOLERANT_FUNCTIONS), and the IS* family answers a question
        # *about* the error rather than failing on it.
        if (
            (name in _VALUE_FUNCTIONS or name in _COMPLEX_FUNCTIONS)
            and name not in _ERROR_TOLERANT_FUNCTIONS
            and name not in _AGGREGATE_ERROR_FUNCTIONS
            and name not in _SCALAR_SLOT_FUNCTIONS
        ):
            for value, from_ref in zip(values, from_reference):
                # COUNTBLANK answers a question about a *cell*, and an error
                # cell is not blank, so one read from a reference is counted
                # rather than propagated. An error written into the formula is
                # not a cell at all and still propagates, which is what the
                # client engine does: COUNTBLANK(A5) is 0 on both sides and
                # COUNTBLANK(1/0) is #DIV/0! on both.
                if is_error(value) and not (name == "COUNTBLANK" and from_ref):
                    return value
        if name in _ADOPTED_STATISTICAL_FUNCTIONS:
            return self._adopted_statistical(name, values, from_reference)
        if name in _ADOPTED_SINGLE_FUNCTIONS:
            return self._adopted_single(name, values)
        if name in _COMPLEX_FUNCTIONS:
            return self._complex(name, values)
        if name in _ENGINEERING_FUNCTIONS:
            return self._engineering(name, values)
        if name in ("SUM", "AVERAGE", "COUNT", "COUNTA", "MIN", "MAX", "PRODUCT"):
            return self._aggregate(name, values, from_reference)
        if name in (
            "ABS",
            "SQRT",
            "INT",
            "LEN",
            "UPPER",
            "LOWER",
            "TRIM",
            "ISBLANK",
            "ISNUMBER",
            "ISTEXT",
        ):
            return self._single(name, values)
        if name in ("ISERROR", "ISNA", "NA", "TYPE"):
            return self._information(name, values)
        if name in ("LEFT", "RIGHT", "MID"):
            return self._text_slice(name, values)
        if name in ("CONCAT", "CONCATENATE"):
            return "".join(_to_text(value) for value in values)
        if name == "EXACT":
            if len(values) != 2:
                return VALUE_ERROR
            return _to_text(values[0]) == _to_text(values[1])
        if name == "PROPER":
            if len(values) != 1:
                return VALUE_ERROR
            # Excel capitalises the first letter of every run of letters and
            # lower-cases the rest, so "hello wORLD" is "Hello World".
            # Splitting on whitespace left "o'neil" and "2-cent" untouched
            # after the separator, which Excel capitalises.
            return re.sub(
                r"[a-zA-Z]+",
                lambda match: match.group(0)[:1].upper() + match.group(0)[1:].lower(),
                _to_text(values[0]),
            )
        if name == "SUBSTITUTE":
            return self._substitute(values)
        if name in ("DATE", "YEAR", "MONTH", "DAY"):
            return self._date(name, values)
        if name == "DAYS":
            return self._days(values)
        # ── Advanced functions ──────────────────────────────────────────────
        if name in ("COUNTBLANK", "COUNTIF", "SUMPRODUCT"):
            return self._advanced_aggregate(name, args, sheet, seen, depth)
        if name in (
            "TEXT",
            "VALUE",
            "NUMBERVALUE",
            "FIND",
            "SEARCH",
            "REPLACE",
            "REPT",
            "CHAR",
            "CODE",
        ):
            # NUMBERVALUE reads each slot as one value (the client's toText
            # scalarizes), so it takes the raw nodes rather than the pooled
            # list: NUMBERVALUE(A1:A3) is #VALUE!, not the first flattened cell.
            if name == "NUMBERVALUE":
                return self._numbervalue(args, sheet, seen, depth)
            return self._advanced_text(name, values)
        if name in ("EOMONTH", "EDATE", "WEEKDAY", "WEEKNUM", "NETWORKDAYS", "WORKDAY"):
            if name in ("NETWORKDAYS", "WORKDAY") and len(args) > 3:
                # A holiday range arrives as one argument no matter how many
                # cells it flattens to, so the raw count is what bounds the
                # call: NETWORKDAYS(1, 1, 1, 1) is #VALUE!. Errors still
                # propagate first, through the shared check above.
                return VALUE_ERROR
            return self._advanced_date(name, values)
        if name in (
            "PI",
            "RADIANS",
            "DEGREES",
            "SIN",
            "COS",
            "TAN",
            "ASIN",
            "ACOS",
            "ATAN",
        ):
            return self._advanced_trig(name, values)
        if name in ("AVERAGEIF", "AVERAGEIFS", "SUMIFS", "COUNTIFS"):
            return self._advanced_criteria(name, args, sheet, seen, depth)
        return NAME_ERROR

    def _expand_arg(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> Tuple[List[Any], bool]:
        """Flatten one argument node into values plus a provenance flag.

        The flag is True when the values were read out of a *reference* -- a
        cell, a ``:`` range, or a block returned by OFFSET -- rather than typed
        into the formula. Excel coerces a text argument into a number but never
        a value that came from a reference (``SUM("2")`` is 2 while
        ``SUM(A1)`` is 0 when A1 holds the text "2"), so the aggregates have to
        be able to tell the two apart.
        """
        kind = node[0]
        if kind == "blank":
            # An omitted argument, which ``_expand_arg`` reports as a blank
            # value the way it would a blank cell.
            return [None], False
        if kind == "ref":
            text = node[1]
            if ":" in text:
                first, second = text.split(":", 1)
                s1, r1, c1 = parse_cell_ref(first, self._named_ranges)
                s2, r2, c2 = parse_cell_ref(second, self._named_ranges)
                return (
                    self._range_values(s1 or sheet, r1, c1, r2, c2, seen, depth),
                    True,
                )
            return [self._eval(node, sheet, seen, depth)], True
        value = self._eval(node, sheet, seen, depth)
        if isinstance(value, list):
            # A function returning a block of values (OFFSET) contributes each of
            # them, exactly the way a range argument does. A taller block is a
            # row-major matrix, so a nested row is flattened into the one value
            # list the pooled readers want.
            flattened: List[Any] = []
            for item in value:
                if isinstance(item, list):
                    flattened.extend(item)
                else:
                    flattened.append(item)
            return flattened, True
        return [value], False

    def _argument_error(
        self,
        args: List[Any],
        sheet: Optional[str],
        seen: set,
        depth: int,
        defer_references: bool = False,
    ) -> Optional[str]:
        """The error a *value argument* raises before the body reads it.

        The client engine evaluates every argument before it calls the
        function, so an argument that evaluates to an error propagates in
        argument order -- ahead of an error cell inside a later range read or
        a length mismatch. A multi-cell range is not such a value: it
        evaluates to a matrix the function reads for itself, so only a single
        value -- a literal, a single-cell reference or an expression -- is
        checked here.

        ``defer_references`` is the flat range reader's rule: it hands over
        *every* reference unevaluated -- a single cell as much as a ``:``
        range -- so both are skipped here and only an expression or a literal
        argument is inspected.
        """
        for arg in args:
            if defer_references:
                if arg[0] == "ref":
                    continue
            elif arg[0] == "ref" and ":" in arg[1]:
                continue
            value = self._eval(arg, sheet, seen, depth)
            if is_error(value):
                return value
        return None

    def _truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if _numeric(value):
            return value != 0
        if isinstance(value, str) and value not in ERROR_CODES:
            # Empty text is falsy, as in Excel (IF("", 1, 2) takes the else
            # branch) and in the client engine.
            return len(value) > 0
        return bool(value)

    def _aggregate(
        self, name: str, values: List[Any], from_reference: List[bool]
    ) -> Any:
        """SUM/AVERAGE/COUNT/COUNTA/MIN/MAX/PRODUCT over flattened arguments.

        ``from_reference`` runs parallel to *values* and marks the entries that
        came out of a reference, because Excel treats the two differently: a
        text *argument* that reads as a number is coerced (``SUM("2")`` is 2)
        while a value read from a reference is not (``SUM(A1)`` is 0 when A1
        holds the text "2").
        """
        if name == "COUNTA":
            return sum(1 for value in values if value is not None and value != "")
        numbers: List[Any] = []
        for value, ranged in zip(values, from_reference):
            if _numeric(value):
                numbers.append(value)
            elif not ranged:
                coerced = _coerce_number_text(value)
                if coerced is not None:
                    numbers.append(coerced)
        if name == "COUNT":
            # Excel's COUNT counts numbers only: an error value or non-numeric
            # text in the arguments is ignored rather than counted, and a text
            # argument counts only when it reads as a number.
            return len(numbers)
        # SUM/AVERAGE/MIN/MAX/PRODUCT propagate an error operand. Filtering it
        # out with the non-numeric cells silently returned a plausible but
        # wrong total (``SUM(1, 1/0)`` was 1, not ``#DIV/0!``). An error
        # *written into* the formula propagates ahead of an error cell in a
        # referenced range, in argument order, so ``SUM(A1:A5, NA())`` is #N/A
        # while ``SUM(A1:A5)`` is the range's #DIV/0!.
        for value, ranged in zip(values, from_reference):
            if is_error(value) and not ranged:
                return value
        for value in values:
            if is_error(value):
                return value
        if name == "SUM":
            return float(sum(numbers))
        if name == "PRODUCT":
            # PRODUCT of no numeric values is 0, not the mathematical empty
            # product of 1: Excel returns 0 for PRODUCT() and for a range that
            # holds no numbers, and the client engine agrees.
            if not numbers:
                return 0.0
            product = 1.0
            for value in numbers:
                product *= value
            return product
        if not numbers:
            if name == "MIN" or name == "MAX":
                return 0
            return DIV_ZERO if name == "AVERAGE" else 0
        if name == "AVERAGE":
            return float(sum(numbers)) / len(numbers)
        if name == "MIN":
            return min(numbers)
        if name == "MAX":
            return max(numbers)
        return VALUE_ERROR

    def _single(self, name: str, values: List[Any]) -> Any:
        # Fixed arity: a stray argument is #VALUE!, the way the client
        # engine answers it. Reading only the first value silently agreed
        # on every valid call while disagreeing on ABS(1, 2) and friends.
        if len(values) != 1:
            return VALUE_ERROR
        value = values[0]
        # The IS* family answers a question *about* its operand, so an error
        # value is ordinary input: ISNUMBER(#DIV/0!) is FALSE rather than the
        # error itself. Every function below propagates it instead.
        if name == "ISBLANK":
            # TRUE only for a genuinely empty cell. A cell holding the empty
            # string (the result of something like =IF(1=2,"","x")) contains
            # text, so ISBLANK("") is FALSE as in Excel.
            return value is None
        if name == "ISNUMBER":
            return _numeric(value)
        if name == "ISTEXT":
            return isinstance(value, str) and value not in ERROR_CODES
        if is_error(value):
            return value
        if name == "ABS":
            return abs(_to_number(value))
        if name == "SQRT":
            number = _to_number(value)
            if number < 0:
                return NUM_ERROR
            return math.sqrt(number)
        if name == "INT":
            return float(math.floor(_to_number(value)))
        if name == "LEN":
            # Excel counts UTF-16 code units, so an astral character (an emoji)
            # is two: LEN("😀") is 2. Python's len counts code points, which
            # answered 1 here while the client engine -- a UTF-16 string by
            # definition -- answered 2.
            return float(_utf16_len(_to_text(value)))
        if name == "UPPER":
            return _to_text(value).upper()
        if name == "LOWER":
            return _to_text(value).lower()
        if name == "TRIM":
            # Excel deletes leading/trailing spaces and collapses every
            # internal run down to one space. ``str.strip()`` only handled the
            # ends, so "a   b" came back unchanged -- and it also stripped
            # tabs, which Excel's TRIM leaves alone because it only knows
            # about spaces.
            return re.sub(r" +", " ", _to_text(value)).strip(" ")
        return VALUE_ERROR

    def _information(self, name: str, values: List[Any]) -> Any:
        """ISERROR, ISNA, NA and TYPE.

        These inspect an error operand instead of propagating it: the IS*
        family answers a question *about* the value, so ``ISERROR(1/0)`` is
        TRUE. That is what makes ``IF(ISERROR(x), ...)`` usable as a guard.
        """
        if name == "NA":
            # NA() takes no arguments and *is* the #N/A error value.
            return VALUE_ERROR if values else N_A_ERROR
        if len(values) != 1:
            return VALUE_ERROR
        value = values[0]
        if name == "ISERROR":
            return is_error(value)
        if name == "ISNA":
            return value == N_A_ERROR
        if name == "TYPE":
            # Excel's type codes: 1 number, 2 text, 4 logical, 16 error.
            # ``is_error`` has to be tested before the text branch because an
            # error value is a string.
            if isinstance(value, bool):
                return 4
            if _numeric(value):
                return 1
            if is_error(value):
                return 16
            if isinstance(value, str):
                return 2
            return 1
        return VALUE_ERROR

    def _scalar_slot(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> Tuple[Optional[str], Any]:
        """Read one scalar slot through the pooled range rule.

        An error cell in the slot propagates, a one-cell slot is that cell
        and anything else is #VALUE! -- the client engine's ``scalarize``.
        Returns ``(error, value)``.
        """
        values, _ = self._expand_arg(node, sheet, seen, depth)
        for value in values:
            if is_error(value):
                return value, None
        if len(values) != 1:
            return VALUE_ERROR, None
        return None, values[0]

    def _scalar_or(self, node: Any, sheet: Optional[str], seen: set, depth: int) -> Any:
        """One slot's single value, or the error the pooled rule raises.

        The client engine's ``scalarize``: a matrix is ``#VALUE!`` unless it
        holds an error cell (which propagates) and a one-cell range is that
        cell. Used where the argument must be a single value -- an IF
        condition or branch, ``NOT``, the ``IFERROR``/``IFNA`` value and
        fallback -- so a nested call that answers a matrix is read the same
        way a range of the same shape is.
        """
        error, value = self._scalar_slot(node, sheet, seen, depth)
        return error if error is not None else value

    @staticmethod
    def _coerce_slot(value: Any) -> Tuple[Optional[str], Optional[float]]:
        """Coerce one scalar slot value, returning ``(error, number)``."""
        if is_error(value):
            return value, None
        try:
            return None, _to_number(value)
        except ValueError as exc:
            message = str(exc)
            return (message if message in ERROR_CODES else VALUE_ERROR), None

    def _double_args(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """ROUND/MOD/POWER/CEILING/FLOOR over raw argument nodes.

        Each slot is reduced and coerced in client order -- the first slot
        first, except ROUND which reads its digits first like the client
        engine's ``roundDigits`` -- so a coercion failure in the earlier slot
        beats an error cell in a later range. The pooled propagation in
        ``_call`` cannot express that: it scans the flattened values first
        and always answers the error cell.
        """
        if len(args) != 2:
            return VALUE_ERROR
        if name in ("ROUND", "ROUNDUP", "ROUNDDOWN"):
            error, raw_digits = self._scalar_slot(args[1], sheet, seen, depth)
            if error is not None:
                return error
            error, digits_number = self._coerce_slot(raw_digits)
            if error is not None or digits_number is None:
                return error or VALUE_ERROR
            error, raw_number = self._scalar_slot(args[0], sheet, seen, depth)
            if error is not None:
                return error
            error, number = self._coerce_slot(raw_number)
            if error is not None or number is None:
                return error or VALUE_ERROR
            try:
                digits = int(digits_number)
            except (ValueError, OverflowError) as exc:
                message = str(exc)
                return message if message in ERROR_CODES else VALUE_ERROR
            digits = max(-15, min(digits, 15))
            factor = 10**digits
            scaled = number * factor
            magnitude = abs(scaled)
            epsilon = min(magnitude * 1e-12, 1e-9)
            if name == "ROUND":
                rounded = math.floor(magnitude + 0.5 + epsilon)
            elif name == "ROUNDUP":
                rounded = math.ceil(magnitude - epsilon)
            else:
                rounded = math.floor(magnitude + epsilon)
            return math.copysign(rounded, scaled) / factor
        error, raw_a = self._scalar_slot(args[0], sheet, seen, depth)
        if error is not None:
            return error
        error, a = self._coerce_slot(raw_a)
        if error is not None or a is None:
            return error or VALUE_ERROR
        error, raw_b = self._scalar_slot(args[1], sheet, seen, depth)
        if error is not None:
            return error
        error, b = self._coerce_slot(raw_b)
        if error is not None or b is None:
            return error or VALUE_ERROR
        if name == "MOD":
            if b == 0:
                return DIV_ZERO
            return a - b * math.floor(a / b)
        if name == "POWER":
            if a == 0 and b < 0:
                return DIV_ZERO
            try:
                return math.pow(a, b)
            except (ValueError, OverflowError):
                return NUM_ERROR
        if name in ("CEILING", "FLOOR"):
            if b == 0:
                return DIV_ZERO
            if a > 0 and b < 0:
                return NUM_ERROR
            if a == 0:
                return 0.0
            same_sign = (a < 0) == (b < 0)
            away_from_zero = same_sign == (name == "CEILING")
            magnitude = abs(a) / abs(b)
            epsilon = min(magnitude * 1e-12, 1e-9)
            if away_from_zero:
                multiple = math.ceil(magnitude - epsilon)
            else:
                multiple = math.floor(magnitude + epsilon)
            result = multiple * abs(b)
            if a < 0:
                result = -result
            return float(f"{result:.15g}")
        return VALUE_ERROR

    def _text_slice(self, name: str, values: List[Any]) -> Any:
        """LEFT, RIGHT, MID with Excel's argument contract.

        ``num_chars`` is optional for LEFT/RIGHT (Excel defaults it to 1) and
        must not be negative; MID additionally requires a 1-based
        ``start_num``. Clamping a negative count to zero instead of failing
        sliced from the wrong end and returned a plausible-looking result
        (``LEFT("hello", -1)`` gave ``"hell"``), hiding the caller's
        mistake instead of surfacing #VALUE!.
        """
        if name == "MID":
            if len(values) != 3:
                return VALUE_ERROR
        elif len(values) < 1 or len(values) > 2:
            return VALUE_ERROR
        if is_error(values[0]):
            return values[0]
        try:
            text = _to_text(values[0])
            if name == "MID":
                start_number = _to_number(values[1])
                count_number = _to_number(values[2])
            else:
                start_number = 1.0
                count_number = _to_number(values[1]) if len(values) == 2 else 1.0
        except (ValueError, OverflowError) as exc:
            return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
        # Test the raw count: Excel rejects a negative one even when it
        # truncates to zero, and the client engine checks the argument before
        # slicing. ``int(-0.5)`` is 0, so checking after the cast let
        # ``LEFT(text, -0.5)`` slice from the start where the client answers
        # #VALUE!.
        if start_number < 1 or count_number < 0:
            return VALUE_ERROR
        start = int(start_number)
        num_chars = int(count_number)
        # Positions and counts are UTF-16 code units, as in Excel and the
        # client engine: LEFT("😀", 1) keeps the lone high surrogate rather
        # than the whole character Python slicing would keep.
        if name == "LEFT":
            return _utf16_slice(text, 0, num_chars)
        if name == "RIGHT":
            # Python's text[-0:] is the whole string, so zero needs its own
            # branch to match the empty result Excel returns.
            if not num_chars:
                return ""
            # A count past the length takes the whole string. Passing the
            # negative ``len - num_chars`` straight to the slice read from the
            # end again instead of clamping to the start, so RIGHT("abcdef",
            # 10) returned the last few characters where Excel -- and the
            # client engine's ``slice(-count)`` -- answer the whole string.
            start = max(_utf16_len(text) - num_chars, 0)
            return _utf16_slice(text, start, None)
        offset = start - 1
        return _utf16_slice(text, offset, offset + num_chars)

    def _range_matrix(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> List[List[Any]]:
        """Resolve a ``A1:C10`` ref node into a 2-D matrix of values.

        A single-cell reference (``B6``) is Excel's 1x1 array rather than an
        empty range, so MATCH, LOOKUP, V/HLOOKUP and SUMPRODUCT read that one
        cell instead of answering #VALUE!/#N/A for a one-cell argument.

        Lookup and criteria functions need the *shape* of a range (rows ×
        columns), which the flat ``_expand_arg`` path discards.

        Anything that is not a ``ref`` node -- a literal, or a nested call
        such as OFFSET/INDEX -- is read the way the client engine reads it: a
        single value is a 1x1 array and a block of values is one row, never an
        empty range. Returning ``[]`` for a literal made every range function
        see no cells at all, so ``COUNTIF("abc", "abc")`` was 0 and
        ``MATCH("a", "a", 0)`` was #N/A where the client answered 1.
        """
        if node[0] != "ref":
            value = self._eval(node, sheet, seen, depth)
            if isinstance(value, list):
                return (
                    [list(row) for row in value]
                    if (value and isinstance(value[0], list))
                    else [list(value)]
                )
            return [[value]]
        if ":" in node[1]:
            first, second = node[1].split(":", 1)
        else:
            first = second = node[1]
        s1, r1, c1 = parse_cell_ref(first, self._named_ranges)
        s2, r2, c2 = parse_cell_ref(second, self._named_ranges)
        target = s1 or sheet
        r_start, r_end = sorted((r1, r2))
        c_start, c_end = sorted((c1, c2))
        matrix: List[List[Any]] = []
        for r in range(r_start, r_end + 1):
            row_values: List[Any] = []
            for c in range(c_start, c_end + 1):
                self._used += 1
                if self._used > self._dependencies:
                    raise ValueError(NUM_ERROR)
                row_values.append(self._resolve(target, r, c, seen, depth + 1))
            matrix.append(row_values)
        return matrix

    def _criteria_range(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> List[List[Any]]:
        """Resolve a criteria/sum range.

        A single cell is a 1x1 range to these functions (``COUNTIF(B6, x)``),
        which ``_range_matrix`` describes.
        """
        return self._range_matrix(node, sheet, seen, depth)

    @staticmethod
    def _lookup_eq(a: Any, b: Any, wildcards: bool = False) -> bool:
        """Equality used by V/HLOOKUP: numeric cells compare numerically,
        anything text-like compares as text.

        ``wildcards`` adds Excel's exact-lookup pattern rule: a *text* key that
        reads as a pattern (``_is_pattern``) matches text cells instead of
        comparing literally, so ``MATCH("ap*", D1:D3, 0)`` finds ``apple`` and
        ``MATCH("tw~*", L1, 0)`` finds the literal ``tw*``. Only text can
        match, the same rule the criteria comparisons use, and the approximate
        forms never pattern-match, so they leave the flag off.
        """
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        if wildcards and isinstance(b, str) and _is_pattern(b):
            return isinstance(a, str) and _wildcard_match(b, a)
        if isinstance(a, str) or isinstance(b, str):
            return _text_equals(a, b)
        if isinstance(a, bool) != isinstance(b, bool):
            # A logical never matches a number, for the same ranking reason
            # ``=`` does not: MATCH(1, {TRUE}, 0) is #N/A, not 1.
            return False
        try:
            return _to_number(a) == _to_number(b)
        except ValueError:
            return _text_equals(a, b)

    def _lookup_value(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """Read a lookup value the way the client engine does.

        A single value -- a literal, an expression or a one-cell reference --
        is that value, but a multi-cell range arrives as a *matrix* that
        compares equal to nothing, so an error cell inside it does not
        propagate: the body coerces a later argument (the index, the match
        type) first and reports its error instead. Reading the range through
        ``_eval`` collapsed it to its error cell and answered #DIV/0! for the
        whole call.
        """
        values, _ = self._expand_arg(node, sheet, seen, depth)
        if len(values) == 1:
            return values[0]
        return list(values)

    def _lookup(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """VLOOKUP / HLOOKUP with exact or sorted approximate matching."""
        if len(args) < 3 or len(args) > 4:
            return VALUE_ERROR
        # The column/row index is coerced before the lookup value or the table
        # is read, the way the client engine orders it: VLOOKUP(A1:A5, 1, "x")
        # is the #VALUE! from the index, not the lookup range's error cell.
        try:
            index = int(_to_number(self._eval(args[2], sheet, seen, depth))) - 1
        except ValueError as exc:
            message = str(exc)
            return message if message in ERROR_CODES else VALUE_ERROR
        if index < 0:
            return VALUE_ERROR
        # The lookup value is read as one *slot*, the way the client engine
        # scalarizes it: an error cell in a multi-cell value propagates and a
        # multi-cell value with no error is #VALUE! (VLOOKUP(A1:A3, 1, 1)).
        # Keeping it a matrix that "compares equal to nothing" answered #N/A
        # where the client propagates the error or reports #VALUE!.
        lookup_error, lookup_scalar = self._scalar_slot(args[0], sheet, seen, depth)
        if lookup_error is not None:
            return lookup_error
        lookup_value = lookup_scalar
        matrix = self._range_matrix(args[1], sheet, seen, depth)
        if not matrix:
            return N_A_ERROR
        # An error written into the table propagates instead of reading as
        # a row that never matches (see _computed_range_error): VLOOKUP over
        # INDIRECT("A1:B5") is #REF!, not #N/A.
        table_error = _computed_range_error(args[1], matrix)
        if table_error is not None:
            return table_error
        range_lookup = True
        if len(args) == 4:
            range_lookup = bool(self._truthy(self._eval(args[3], sheet, seen, depth)))

        if name == "HLOOKUP":
            if not matrix[0]:
                return N_A_ERROR
            if index >= len(matrix):
                return REF_ERROR
            key_row = matrix[0]
            for c, key in enumerate(key_row):
                if self._lookup_eq(key, lookup_value, not range_lookup):
                    return matrix[index][c]
            if range_lookup:
                try:
                    target = _to_number(lookup_value)
                except ValueError:
                    return N_A_ERROR
                best = None
                for c, key in enumerate(key_row):
                    if key is None or key == "":
                        continue
                    try:
                        if _to_number(key) <= target:
                            best = matrix[index][c]
                        else:
                            break
                    except ValueError:
                        continue
                return best if best is not None else N_A_ERROR
            return N_A_ERROR

        # VLOOKUP: search the first column.
        if index >= len(matrix[0]):
            return REF_ERROR
        for row in matrix:
            if not row:
                continue
            if self._lookup_eq(row[0], lookup_value, not range_lookup):
                return row[index]
        if range_lookup:
            try:
                target = _to_number(lookup_value)
            except ValueError:
                return N_A_ERROR
            best = None
            for row in matrix:
                if not row:
                    continue
                key = row[0]
                if key is None or key == "":
                    continue
                try:
                    if _to_number(key) <= target:
                        best = row[index]
                    else:
                        break
                except ValueError:
                    continue
            return best if best is not None else N_A_ERROR
        return N_A_ERROR

    def _criteria(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """SUMIF / COUNTIF / AVERAGEIF with operator-prefixed criteria."""
        if len(args) < 2 or len(args) > 3:
            return VALUE_ERROR
        matrix = self._criteria_range(args[0], sheet, seen, depth)
        if not matrix:
            return DIV_ZERO if name == "AVERAGEIF" else 0.0
        # An error written into the range propagates; an error cell only
        # fails to match (see _computed_range_error).
        range_error = _computed_range_error(args[0], matrix)
        if range_error is not None:
            return range_error
        first = args[1]
        if first[0] == "str":
            criteria: Any = first[1]
        elif first[0] == "num":
            criteria = first[1]
        else:
            # A criteria argument is a single value, so a matrix -- a range or
            # a nested call -- follows the pooled rule (the client's
            # scalarize): SUMIF(1, FREQUENCY(A1:A5, A1:A5)) is #VALUE! and
            # SUMIF(1, OFFSET(A1, 0, 0, 5, 1)) propagates the error cell.
            criteria = self._scalar_or(first, sheet, seen, depth)
        if is_error(criteria) and first[0] != "ref":
            # An error *written* as the criteria propagates straight away --
            # SUMIF(1, 1/0, NA()) is #DIV/0! and SUMIF(1, NA(), 1/0) is #N/A.
            return criteria
        if name == "COUNTIF" and len(args) != 2:
            # COUNTIF takes exactly two arguments -- SUMIF and AVERAGEIF take
            # an optional sum range, but a third argument to COUNTIF is
            # #VALUE!.
            return VALUE_ERROR
        sum_matrix = matrix
        if len(args) == 3:
            sum_matrix = self._criteria_range(args[2], sheet, seen, depth)
            sum_error = _computed_range_error(args[2], sum_matrix)
            if sum_error is not None:
                return sum_error
        if is_error(criteria):
            # A criteria read out of a *cell* propagates too, but only after
            # the summed range's own error: SUMIF(1, A5, NA()) is the summed
            # range's #N/A while SUMIF(1, A5, 1) is the criteria cell's #DIV/0!.
            return criteria
        total = 0.0
        count = 0
        for r in range(min(len(matrix), len(sum_matrix))):
            row = matrix[r]
            sum_row = sum_matrix[r]
            for c in range(min(len(row), len(sum_row))):
                if self._criteria_test(criteria, row[c]):
                    value = sum_row[c]
                    if name == "AVERAGEIF" and not _numeric(value):
                        # Excel averages only the numeric entries of the
                        # average range: text and blanks are ignored, so a
                        # match with no number at all yields #DIV/0!.
                        continue
                    total += _to_number_or_zero(value)
                    count += 1
        if name == "SUMIF":
            return float(total)
        if name == "COUNTIF":
            return float(count)
        if name == "AVERAGEIF":
            return float(total) / count if count else DIV_ZERO
        return VALUE_ERROR

    @staticmethod
    def _criteria_test(criteria: Any, value: Any) -> bool:
        """Match ``value`` against an Excel-style criteria (e.g. ``">2"``).

        An operator-prefixed criteria compares through ``_compare_values``, so
        the cross-type ordering and case-insensitive text matching are Excel's;
        the plain form is an exact (case-insensitive) text match.
        """
        if is_error(value):
            # An error *cell* inside a criteria range only fails to match; an
            # error *criteria* argument is what propagates (handled by _criteria).
            return False

        if criteria is None:
            return value is None or value == ""
        if isinstance(criteria, str):
            for op in (">=", "<=", "<>", ">", "<", "="):
                if criteria.startswith(op) and len(criteria) > len(op):
                    return _criteria_operator(op, value, criteria[len(op) :].strip())
            if _is_pattern(criteria):
                # A bare pattern (``COUNTIF(range, "tw*")``) is Excel's ``=``
                # with wildcards, escapes honoured: only text cells can match
                # it, so ``COUNTIF(range, "tw~*")`` finds the text ``tw*``.
                return isinstance(value, str) and _wildcard_match(criteria, value)
            return _fold_case(_to_text(value)) == _fold_case(criteria)
        if isinstance(value, bool) or isinstance(criteria, bool):
            # Criteria rank types the way every other comparison here does, so
            # a logical criterion matches only a logical cell of the same
            # value: COUNTIF(range, 1) does not count TRUE.
            return (
                isinstance(value, bool)
                and isinstance(criteria, bool)
                and value == criteria
            )
        if isinstance(value, str):
            return value == criteria
        if value is None:
            # A blank is not the number it coerces to in arithmetic, so
            # COUNTIF(range, 0) counts zero cells rather than every empty one.
            return False
        try:
            return _to_number(value) == _to_number(criteria)
        except ValueError:
            return False
