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
  before the lookup ran, instead of reading cell by cell;
* a binary operator evaluated the right operand before inspecting the left,
  so a *thrown* right-side failure outranked an error *cell* on the left
  (``A5+NA()`` was ``#N/A``) instead of answering the left error ``#DIV/0!``;
* the exact ``MATCH`` key was kept a matrix that "compares equal to nothing"
  (``MATCH(A1:A5, A1:A5, 0)`` answered ``#N/A``) instead of reading it as one
  scalar slot -- an error cell propagates and a multi-cell key is ``#VALUE!``;
* ``&`` shared the additive precedence level, so ``1&1+1`` parsed as
  ``(1&1)+1`` (the number 12) instead of Excel's ``1&(1+1)`` (the text "12");
* a binary operator evaluated its right operand before answering a left error,
  so a *raised* right-side failure outranked it (``NA()+A1:A5/1`` answered
  #DIV/0!) where the client answers the left error;
* a unary ``-``/``%`` coercion failure raised instead of yielding an error
  value, so ``IFERROR(-"abc", 9)`` was #VALUE! rather than 9.

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
    # The three-operand slot-coercion order, per family: an earlier slot's
    # coercion failure or error beats an error cell in a later range, while a
    # written error still propagates in argument order.
    ('MID(1,"x",A1:A5)', "error:#VALUE!"),
    ('DATE(1,"x",A1:A5)', "error:#VALUE!"),
    ('TIME(A1:A5,"x",NA())', "error:#N/A"),
    ('OFFSET(A1,"x",1/0)', "error:#VALUE!"),
    # A multi-cell lookup value is one slot: its error cell propagates and a
    # multi-cell value of plain values is #VALUE!.
    ("VLOOKUP(A1:A5,1,1)", "error:#DIV/0!"),
    ("HLOOKUP(A1:A5,A1,1)", "error:#DIV/0!"),
    # XLOOKUP settles the arrays and their shape first, then the lookup value.
    ("XLOOKUP(A1:A5,1,1)", "error:#DIV/0!"),
    ("XLOOKUP(A1:A5,1,NA())", "error:#N/A"),
    ("XLOOKUP(A1:A5,1,A1:A5)", "error:#VALUE!"),
    # A *computed* scalar error in the lookup or return array propagates,
    # while a plain reference keeps its error as a cell that fails to match
    # (and unary plus, the identity, still reads as that reference).
    ("XLOOKUP(1,SUM(A1:A5),1)", "error:#DIV/0!"),
    ("XLOOKUP(1,1,SUM(A1:A5))", "error:#DIV/0!"),
    ("XLOOKUP(1,SUM(A1:A5),SUM(A1:A5))", "error:#DIV/0!"),
    ('XLOOKUP(99,A1:A2,SUM(A1:A5),"nf")', "error:#DIV/0!"),
    ("XLOOKUP(1,A5,1)", "error:#N/A"),
    ("XLOOKUP(1,+A5,1)", "error:#N/A"),
    ("XLOOKUP(1,-A5,1)", "error:#DIV/0!"),
    ("XLOOKUP(1,+A1:A5,1)", "error:#VALUE!"),
    # A written criteria error propagates first, then the summed range's, then
    # a criteria read out of a cell.
    ("SUMIF(1,1/0,NA())", "error:#DIV/0!"),
    ("SUMIF(1,NA(),1/0)", "error:#N/A"),
    ("SUMIF(1,A5,NA())", "error:#N/A"),
    ("SUMIF(1,A5,1)", "error:#DIV/0!"),
    ("AVERAGEIF(1,A5,1)", "error:#DIV/0!"),
    # The fixed-arity text bodies read their slots in body order and scalarize
    # each, so an earlier slot's coercion beats an error cell in a later range.
    ("LEFT(A1:A3,A1:A5)", "error:#VALUE!"),
    ("RIGHT(A1:A3,A1:A5)", "error:#VALUE!"),
    ("EXACT(A1:A3,A1:A5)", "error:#VALUE!"),
    ("SUBSTITUTE(A1:A3,A1:A3,A1:A5)", "error:#VALUE!"),
    ('REPLACE(1,"x",A1:A5,1)', "error:#VALUE!"),
    ("FIND(A1:A3,A1:A5)", "error:#VALUE!"),
    ("SEARCH(A1:A3,A1:A5)", "error:#VALUE!"),
    ("REPT(A1:A3,A1:A5)", "error:#VALUE!"),
    # TEXT reads its format before its value, NUMBERVALUE its separators.
    ("TEXT(A1:A5,A1:A3)", "error:#VALUE!"),
    ("NUMBERVALUE(A1:A5,A1:A3)", "error:#VALUE!"),
    # A written error argument propagates eagerly through the *IF family.
    ("COUNTIFS(A1:A3,A1:A3,1,1/0)", "error:#DIV/0!"),
    # Criteria and sum ranges align by flat index, so a function-returned
    # matrix or a row against a column compares every position.
    ("SUMIF(FREQUENCY(A1:A5,A1:A5),1,A1:A5)", "number:6"),
    ("SUMIFS(A1:A3,FREQUENCY(A1:A5,A1:A5),1)", "number:6"),
    ("SUMIF(A1:C1,10,A1:A3)", "number:3"),
    # A range operand in a binary operator keeps its matrix until the operator
    # applies it, then is scalarized.
    ("A1:A3+A5", "error:#DIV/0!"),
    ("A1:A3-A5", "error:#DIV/0!"),
    ("A1:A3=A1:A3", "error:#VALUE!"),
    ("A1:A3=1", "error:#VALUE!"),
    ("A1:A3&1", "error:#VALUE!"),
    ("A1:A3/1", "error:#VALUE!"),
    ("1+A1:A3", "error:#VALUE!"),
    # A binary operator answers the left operand's error before evaluating the
    # right: an error cell on the left beats a thrown failure on the right.
    ("A5+NA()", "error:#DIV/0!"),
    ("(A5)+NA()", "error:#DIV/0!"),
    ("A5&NA()", "error:#DIV/0!"),
    ('A5+ABS("x")', "error:#DIV/0!"),
    ("NA()+A5", "error:#N/A"),
    # MATCH reads its key as one scalar slot in the exact branch -- an error
    # cell propagates, a multi-cell key is #VALUE! -- while the approximate
    # branches rank the key and answer #N/A.
    ("MATCH(A1:A5,A1:A5,0)", "error:#DIV/0!"),
    ("MATCH(A1:A2,A1:A5,0)", "error:#VALUE!"),
    ("MATCH(A1:A5,A1:A5,1)", "error:#DIV/0!"),
    ("MATCH(A1:A2,A1:A5,1)", "error:#VALUE!"),
    ("LOOKUP(A1:A2,A1:A5)", "error:#VALUE!"),
    ("XLOOKUP(A1:A2,A1:A5,1)", "error:#VALUE!"),
    # ``&`` binds looser than arithmetic and tighter than comparison, so it
    # sits on its own level between them (Excel's order).
    ("1&1+1", "text:12"),
    ("1&2*3", "text:16"),
    ("1=1&1", "boolean:false"),
    ("1&1=1", "boolean:false"),
    ("1<2&3", "boolean:true"),
    # The right operand of a binary operator is not evaluated once the left has
    # failed, even when the right would *raise* (a nested coercion failure).
    ("NA()+A1:A5/1", "error:#N/A"),
    ('A5+-"abc"', "error:#DIV/0!"),
    ('A5+"abc"%', "error:#DIV/0!"),
    # A unary coercion failure is an error *value*, so an error-catching
    # function sees it.
    ('-"abc"', "error:#VALUE!"),
    ('IFERROR(-"abc",9)', "number:9"),
    ("IFERROR(K1%,9)", "number:9"),
    # GCD/LCM read their arguments one at a time, so an earlier negative is
    # #NUM! before a later argument is coerced -- GCD(-1, "x") is #NUM!, not
    # the #VALUE! the text alone would earn.
    ("GCD(-1,1)", "error:#NUM!"),
    ('GCD(-1,"x")', "error:#NUM!"),
    ('LCM(-1,"x")', "error:#NUM!"),
    ('GCD(1,"x")', "error:#VALUE!"),
    # XLOOKUP reads its lookup array before its return array, so a return
    # array that raises does not pre-empt the lookup array's error.
    ("XLOOKUP(0,1/0,LCM(-1,1))", "error:#DIV/0!"),
    ("XLOOKUP(0,1,NA())", "error:#N/A"),
    ("XLOOKUP(0,NA(),1/0)", "error:#N/A"),
    # Excel keeps only 15 significant digits, so the serial rounds there before
    # the time of day is read: 28/3 is stored as 9.33333333333333 (07:59:59),
    # while 0.9999999999999999 rounds up to 1 (midnight).
    ("HOUR(28/3)", "number:7"),
    ("MINUTE(28/3)", "number:59"),
    ("SECOND(28/3)", "number:59"),
    ("HOUR(9.333333333333332)", "number:7"),
    ("MINUTE(9.333333333333332)", "number:59"),
    ("SECOND(9.333333333333332)", "number:59"),
    ("HOUR(0.9999999999999999)", "number:0"),
    ("MINUTE(0.9999999999999999)", "number:0"),
    ("SECOND(0.9999999999999999)", "number:0"),
    # The same storage rule decides a date part, because the serial is stored
    # before either a date or a time reader sees it: 45291.99999999999 rounds
    # to 45292 (2024-01-01), so every date part answers the boundary's. The
    # rule is symmetric about zero (-0.9999999999999999 stores as -1).
    ("YEAR(45291.99999999999)", "number:2024"),
    ("MONTH(45291.99999999999)", "number:1"),
    ("DAY(45291.99999999999)", "number:1"),
    ("WEEKDAY(45291.99999999999)", "number:2"),
    ("YEAR(-0.9999999999999999)", "number:1899"),
    ("MONTH(-0.9999999999999999)", "number:12"),
    ("DAY(-0.9999999999999999)", "number:29"),
    ("WEEKDAY(-0.9999999999999999)", "number:6"),
    # DAYS, EDATE, EOMONTH, WORKDAY and NETWORKDAYS read a serial they round
    # the same way, so each stays on the day the stored value names.
    ("DAYS(10.9, 0.1)", "number:10"),
    ("DAYS(0.9, 0.1)", "number:0"),
    ("DAYS(45291.99999999999, 45292)", "number:0"),
    ("EDATE(45291.99999999999, 1)", "number:45323"),
    ("EOMONTH(45291.99999999999, 1)", "number:45351"),
    ("WORKDAY(45291.99999999999, 1)", "number:45293"),
    ("NETWORKDAYS(45291.99999999999, 45292)", "number:1"),
    # The covered range is checked on the rounded part, not the raw serial:
    # 2958465.999999999 stores as 2958466, outside the span, so it is #NUM!.
    ("YEAR(2958465.5)", "number:9999"),
    ("YEAR(2958465.999999999)", "error:#NUM!"),
    # A holiday names the day its rounded serial's integer part (truncated
    # toward zero): 45291.99999999999 counts as 45292, and -2.5 as -2.
    ("NETWORKDAYS(45292, 45292, 45291.99999999999)", "number:0"),
    ("NETWORKDAYS(-2, -2, -2.5)", "number:0"),
    ("WORKDAY(-3, 1, -2.5)", "number:-1"),
    # A reversed span counts negative, as Excel does: the same workdays are
    # counted forward, then negated, so a holiday counts either way.
    ("NETWORKDAYS(DATE(2024,1,10),DATE(2024,1,1))", "number:-8"),
    ("NETWORKDAYS(DATE(2024,1,1),DATE(2024,1,10),DATE(2024,1,2))", "number:7"),
    ("NETWORKDAYS(DATE(2024,1,10),DATE(2024,1,1),DATE(2024,1,2))", "number:-7"),
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
