"""The call contract both engines share, pinned to its answers.

A wider differential fuzz (every client function at arities zero to four,
fractional time serials, conversions with two bad arguments, computed errors
in criteria ranges, lookups over ranges holding an error cell) found the
corners below, where this engine disagreed with the client engine:

* a stray argument was silently ignored (``ABS(1, 2)`` was 1) instead of
  answering ``#VALUE!``;
* ``SECOND`` rounded to the nearest second (``SECOND`` of 41.8 seconds was
  42) instead of truncating, the way the client engine's millisecond clock
  -- and Excel -- reads it;
* a conversion with a bad number *and* bad places reported the number
  (``BIN2OCT(18, "abc")`` was ``#NUM!``) while the client coerces places
  first and answers ``#VALUE!``;
* an error written into a criteria range was skipped (``COUNTIF(1/0, 1)``
  was 0) instead of propagating, while an error *cell* still only fails
  to match (``COUNTIF(A5, 1)`` is 0 when A5 holds ``1/0``);
* a lookup over a range holding an error cell failed outright
  (``INDEX(A1:A5, 2)`` was ``#DIV/0!``) because the ranges were expanded
  before the lookup ran, instead of reading cell by cell.

Each answer is written the way the shared table writes one --
``kind:value`` -- and a finite number is compared with a tolerance.
"""

import pytest

from src.tests.suite.test_sum_cross_engine import _evaluate

# Each case names the formula and the answer both engines give for it. The
# grid is the shared parity table's: A1 holds 1 and A5 holds the formula
# 1/0, so A5 is the error cell the range cases read.
_CASES = [
    # A stray argument is #VALUE!, on every fixed-arity scalar function.
    ("ABS(1,2)", "error:#VALUE!"),
    ("SIN(1,2)", "error:#VALUE!"),
    ("SQRT(4,9)", "error:#VALUE!"),
    ("INT(1,2)", "error:#VALUE!"),
    ('LEN("a","b")', "error:#VALUE!"),
    ('UPPER("a","b")', "error:#VALUE!"),
    ('LOWER("a","b")', "error:#VALUE!"),
    ('TRIM("a","b")', "error:#VALUE!"),
    ("ISNUMBER(1,2)", "error:#VALUE!"),
    ("ISTEXT(1,2)", "error:#VALUE!"),
    ("ISBLANK(A1,A1)", "error:#VALUE!"),
    ("DEGREES(1,2)", "error:#VALUE!"),
    ("RADIANS(1,2)", "error:#VALUE!"),
    ("COS(1,2)", "error:#VALUE!"),
    ("TAN(1,2)", "error:#VALUE!"),
    ("ASIN(1,2)", "error:#VALUE!"),
    ("ACOS(1,2)", "error:#VALUE!"),
    ("ATAN(1,2)", "error:#VALUE!"),
    ("POWER(2,3,4)", "error:#VALUE!"),
    ("MOD(5,2,1)", "error:#VALUE!"),
    ("ROUND(1.5,0,1)", "error:#VALUE!"),
    ("ROUNDUP(1.5,0,1)", "error:#VALUE!"),
    ("ROUNDDOWN(1.5,0,1)", "error:#VALUE!"),
    ("CEILING(1,1,1)", "error:#VALUE!"),
    ("FLOOR(1,1,1)", "error:#VALUE!"),
    # COUNTIF takes exactly two arguments; SUMIF and AVERAGEIF take three.
    ("COUNTIF(1,1,1)", "error:#VALUE!"),
    ("SUMIF(1,1,1)", "number:1"),
    # A holiday range is one argument however many cells it holds, so only
    # a fourth *argument* is #VALUE!.
    ("NETWORKDAYS(1,2,3,4)", "error:#VALUE!"),
    ("WORKDAY(1,1,1,1)", "error:#VALUE!"),
    ("NETWORKDAYS(1,2)", "number:1"),
    # The time of day truncates: 41.8 seconds is the 41st second, while a
    # whole-second construction still reads exactly.
    ("SECOND(0.04701185841617224)", "number:41"),
    ("SECOND(0.00001)", "number:0"),
    ("SECOND(TIME(1,7,42))", "number:42"),
    ("MINUTE(TIME(12,34,56))", "number:34"),
    ("HOUR(TIME(12,34,56))", "number:12"),
    # Places is coerced before the conversion input is validated.
    ('BIN2OCT(18,"abc")', "error:#VALUE!"),
    ('BIN2OCT(18.985,"abc")', "error:#VALUE!"),
    ('HEX2BIN("12345678901","x")', "error:#VALUE!"),
    ('DEC2BIN(5,"x")', "error:#VALUE!"),
    # An error written into a criteria range propagates ...
    ("COUNTIF(1/0,1)", "error:#DIV/0!"),
    ("SUMIF(1/0,1)", "error:#DIV/0!"),
    ("SUMIF(1,1,1/0)", "error:#DIV/0!"),
    ("SUMIFS(1/0,1,1)", "error:#DIV/0!"),
    ("COUNTIFS(1/0,1)", "error:#DIV/0!"),
    ("COUNTIF(1/0,NA())", "error:#DIV/0!"),
    ("SUMIF(NA(),1,1/0)", "error:#N/A"),
    # ... while an error cell only fails to match.
    ("COUNTIF(A5,1)", "number:0"),
    ("SUMIF(A5,1)", "number:0"),
    ("COUNTIF(A1:A5,2)", "number:1"),
    ("SUMIF(A1:A5,1,A1:A5)", "number:1"),
    # A lookup reads cell by cell, so an error cell it never touches is
    # not its answer.
    ("INDEX(A1:A5,2)", "number:2"),
    ("MATCH(2,A1:A5,0)", "number:2"),
    ("LOOKUP(2,A1:A5)", "number:2"),
    ("XLOOKUP(2,A1:A5,A1:A5)", "number:2"),
    ("COUNTIF(OFFSET(A1,0,0,5,1),2)", "number:1"),
    # ... but a computed error still propagates out of one.
    ('COUNTIF(INDIRECT("A5"),1)', "error:#DIV/0!"),
    ("INDEX(A1:A3,1/0)", "error:#DIV/0!"),
    ("MATCH(1/0,A1:A3,0)", "error:#DIV/0!"),
    # The clock takes no arguments: a stray one is #VALUE!, as Excel
    # rejects it, rather than the time the client engine used to answer.
    ("NOW(1)", "error:#VALUE!"),
    ("TODAY(1)", "error:#VALUE!"),
    ("TODAY(A1)", "error:#VALUE!"),
    # A range that never resolves propagates out of every lookup: the
    # INDIRECT below is #REF! on both engines, so no match is attempted.
    ('MATCH(2,INDIRECT("A1:A5"),0)', "error:#REF!"),
    ('LOOKUP(2,INDIRECT("A1:A5"))', "error:#REF!"),
    ('XLOOKUP(2,INDIRECT("A1:A5"),A1:A5)', "error:#REF!"),
    ('VLOOKUP(2,INDIRECT("A1:B5"),1,FALSE)', "error:#REF!"),
    ('COUNTIF(INDIRECT("A1:A3"),2)', "error:#REF!"),
    # A nested call that fails yields its error *value* rather than raising
    # past the enclosing function, so an error-tolerant caller can classify or
    # skip it: COUNT skips it (0), the IS* family answers *about* it and PI
    # ignores its arguments. Raising instead made COUNT(ABS("x")) #VALUE!
    # rather than 0 and PI(ABS("x")) #VALUE! rather than pi.
    ('COUNT(ABS("x"))', "number:0"),
    ('COUNTA(ABS("x"))', "number:1"),
    ('ISERROR(ABS("x"))', "boolean:true"),
    ('ISNA(ABS("x"))', "boolean:false"),
    ('TYPE(ABS("x"))', "number:16"),
    ('ISBLANK(ABS("x"))', "boolean:false"),
    ('ISNUMBER(ABS("x"))', "boolean:false"),
    ('ISTEXT(ABS("x"))', "boolean:false"),
    ('IFERROR(ABS("x"),42)', "number:42"),
    ('SUM(1,ABS("x"))', "error:#VALUE!"),
]


@pytest.mark.parametrize("formula,expected", _CASES, ids=[case[0] for case in _CASES])
def test_call_contract_matches_the_pinned_answer(formula, expected):
    kind, _, literal = expected.partition(":")
    result = _evaluate(formula)
    assert result["kind"] == kind, f"{formula} -> {result!r}, expected {expected!r}"
    if kind == "number":
        assert result["value"] == pytest.approx(float(literal), abs=1e-9)
    elif kind == "boolean":
        assert result["value"] == (literal == "true")
    elif kind != "blank":
        assert result["value"] == literal
