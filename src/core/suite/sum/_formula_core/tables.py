"""Function-name families the dispatcher switches on.

Kept apart from the dispatch code so the tables stay reviewable: adding a
function means adding it here and in the matching mixin, and the audit tests
compare these sets against the client engine's registry.
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
        # The *IF/*IFS family reads its ranges itself (so a range is deferred)
        # but the client engine still evaluates a *written* argument eagerly,
        # so an error operand in a later argument propagates ahead of an
        # earlier criteria's #VALUE!:
        # COUNTIFS(A1:A3, A1:A3, 1, 1/0) and
        # SUMIF(A1:A3, FREQUENCY(A1:A3, A1:A3), 1/0) are #DIV/0!.
        "SUMIF",
        "COUNTIF",
        "AVERAGEIF",
        "SUMIFS",
        "COUNTIFS",
        "AVERAGEIFS",
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
            # The fixed-arity text functions read each argument as one slot
            # too, in their own body order: a clean multi-cell range where one
            # value belongs is #VALUE! and an error cell in a slot propagates,
            # whichever slot the body reaches first (`_slot_text` propagates an
            # error value, `_to_number` already raises). Pooling them let an
            # error cell in a later range decide the answer before an earlier
            # slot's #VALUE! (LEFT(A1:A3, A1:A5)).
            "LEFT",
            "RIGHT",
            "EXACT",
            "SUBSTITUTE",
            "REPLACE",
            "TEXT",
            "FIND",
            "SEARCH",
            "REPT",
            "NUMBERVALUE",
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
