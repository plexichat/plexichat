"""Unit tests for the bounded PlexiSum formula engine."""

import datetime

from src.core.suite.sum.formulas import (
    FormulaEvaluator,
    MAX_CALL_DEPTH,
    MAX_DEPENDENCIES,
    NUM_ERROR,
    VALUE_ERROR,
)


def _resolver(data):
    def resolver(sheet, row, col):
        return data.get((str(sheet), int(row), int(col)))

    return resolver


def _grid():
    """A2-style grid: A1=10, B1=20, A2=5, B2='hi'."""
    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 10, "formula": None},
        ("1", 0, 1): {"value_type": "number", "value": 20, "formula": None},
        ("1", 1, 0): {"value_type": "number", "value": 5, "formula": None},
        ("1", 1, 1): {"value_type": "text", "value": "hi", "formula": None},
    }
    return _resolver(cells)


def _eval(formula, resolver=None, sheet="1", row=9, col=9):
    return FormulaEvaluator(resolver or _grid()).evaluate_cell(sheet, row, col, formula)


def test_arithmetic_precedence_and_parens():
    assert _eval("1+2*3") == 7.0
    assert _eval("(1+2)*3") == 9.0
    assert _eval("(A1+A2)*2") == 30.0
    assert _eval("2^3") == 8.0
    assert _eval("ABS(-5)") == 5.0
    assert _eval("A1/0") == "#DIV/0!"


def test_comparisons_and_equality():
    assert _eval("A1>15") is False
    assert _eval("A1>=10") is True
    assert _eval("B1=5") is False
    assert _eval("2=2") is True
    assert _eval("2<>2") is False


def test_string_concatenation():
    assert _eval('B2 & "!"') == "hi!"
    assert _eval('A1&"x"') == "10x"
    assert _eval('CONCAT(A1,"|",B2)') == "10|hi"


def test_aggregate_functions():
    assert _eval("SUM(A1:B2)") == 35.0
    assert _eval("SUM(A1,A2,5)") == 20.0
    assert _eval("AVERAGE(A1,A2,5)") == 20 / 3
    assert _eval("COUNT(A1:B2)") == 3
    assert _eval("COUNTA(A1:B2)") == 4
    assert _eval("MIN(A1:A2,3)") == 3.0
    assert _eval("MAX(A1:B2)") == 20.0
    assert _eval("PRODUCT(A1,A2)") == 50.0


def test_single_and_double_functions():
    assert _eval("ROUND(AVERAGE(A1,A2),0)") == 8.0
    assert _eval("LEN(B2)") == 2.0
    assert _eval("UPPER(B2)") == "HI"
    assert _eval("SQRT(16)") == 4.0
    assert _eval("MOD(7,3)") == 1.0
    assert _eval("POWER(2,10)") == 1024.0
    assert _eval("LEFT(B2,1)") == "h"
    assert _eval('TRIM("  x  ")') == "x"
    # Excel collapses internal runs of spaces, not just the ends.
    assert _eval('TRIM("  a    b  ")') == "a b"
    assert _eval("ISNUMBER(A1)") is True
    assert _eval("ISTEXT(B2)") is True
    assert _eval("ISBLANK(B1)") is False


def test_value_and_numbervalue_parse_a_whole_numeric_string():
    """VALUE/NUMBERVALUE must read the whole string, not just a prefix.

    They used to hand the text straight to ``float``, which understood no
    thousands separators, currency symbol, percent sign or parenthesised
    negative (``VALUE("1,234")`` was #VALUE!), and which quietly accepts
    underscore separators (``float("1_0") == 10.0``).
    """
    assert _eval('VALUE("1,234.5")') == 1234.5
    assert _eval('VALUE("1.5")') == 1.5
    assert _eval('VALUE("1.5abc")') == VALUE_ERROR
    assert _eval('VALUE("1_000")') == VALUE_ERROR
    assert _eval('VALUE("")') == VALUE_ERROR
    # Excel also reads a leading currency symbol, a trailing percent sign and
    # a parenthesised negative.
    assert _eval('VALUE("$1,000")') == 1000.0
    assert _eval('VALUE("12%")') == 0.12
    assert _eval('VALUE("(100)")') == -100.0
    # NUMBERVALUE keeps its locale separators but is just as strict.
    assert _eval('NUMBERVALUE("2.500,27", ",", ".")') == 2500.27
    assert _eval('NUMBERVALUE("3.5%")') == 0.035
    assert _eval('NUMBERVALUE("1.5abc")') == VALUE_ERROR


def test_if_and_boolean_functions():
    assert _eval("IF(A1>15,1,2)") == 2.0
    assert _eval("IF(TRUE,1,2)") == 1.0
    assert _eval("AND(TRUE,FALSE)") is False
    assert _eval("OR(TRUE,FALSE)") is True
    assert _eval("NOT(FALSE)") is True


def test_cross_sheet_quoted_sheet_name():
    """The default sheet is 'Sheet 1' (with a space), so quoted refs must work."""
    cells = {
        ("Sheet 1", 0, 0): {"value_type": "number", "value": 10, "formula": None},
        ("Sheet 2", 0, 0): {
            "value_type": "formula",
            "value": None,
            "formula": "'Sheet 1'!A1*2",
        },
    }
    result = _eval(
        "'Sheet 1'!A1*2", resolver=_resolver(cells), sheet="Sheet 2", row=0, col=0
    )
    assert result == 20.0


def test_unquoted_cross_sheet_name():
    cells = {
        ("Sheet2", 0, 0): {"value_type": "number", "value": 7, "formula": None},
        ("Sheet3", 0, 0): {
            "value_type": "formula",
            "value": None,
            "formula": "Sheet2!A1+1",
        },
    }
    assert (
        _eval("Sheet2!A1+1", resolver=_resolver(cells), sheet="Sheet3", row=0, col=0)
        == 8.0
    )


def test_formula_dependency_resolves_live_value():
    """A formula cell referencing another formula cell computes transitively."""
    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 4, "formula": None},
        ("1", 0, 1): {"value_type": "formula", "value": None, "formula": "A1*2"},
        ("1", 1, 1): {"value_type": "formula", "value": None, "formula": "B1+1"},
    }
    assert _eval("B2", resolver=_resolver(cells), row=1, col=1) == 9.0


def test_cycle_detection():
    cells = {
        ("Cyc", 0, 0): {"value_type": "formula", "value": None, "formula": "A2"},
        ("Cyc", 1, 0): {"value_type": "formula", "value": None, "formula": "A1"},
    }
    assert (
        FormulaEvaluator(_resolver(cells)).evaluate_cell("Cyc", 0, 0, "A2") == "#CYCLE!"
    )


def test_self_reference_is_cycle():
    cells = {
        ("S", 0, 0): {"value_type": "formula", "value": None, "formula": "A1+1"},
    }
    assert (
        FormulaEvaluator(_resolver(cells)).evaluate_cell("S", 0, 0, "A1+1") == "#CYCLE!"
    )


def test_unknown_function_and_error_propagation():
    assert _eval("FOO(1)") == "#NAME?"
    assert _eval("A1+FOO(1)") == "#NAME?"
    assert _eval("1+A1/0") == "#DIV/0!"


def test_blank_cells_evaluate_as_empty():
    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 3, "formula": None},
    }
    # B1 and A2 do not exist: blanks contribute 0 to sums and sort last.
    assert _eval("SUM(A1,B1)", resolver=_resolver(cells)) == 3.0
    assert _eval("A2+0", resolver=_resolver(cells)) == 0.0


def test_string_number_coercion_errors():
    cells = {
        ("1", 0, 0): {"value_type": "text", "value": "abc", "formula": None},
    }
    assert _eval("A1+1", resolver=_resolver(cells)) == VALUE_ERROR


def test_bounded_dependency_depth():
    """A short dependency chain resolves; an over-long chain terminates safely.

    ``A{n}`` refers to row ``n - 1`` (0-based), so a formula in row ``i`` of
    ``A{i + 2}`` points at row ``i + 1``: a linear chain down the column.
    """

    def chain(length, terminal=1):
        cells = {}
        for i in range(length):
            cells[("B", i, 0)] = {
                "value_type": "formula",
                "value": None,
                "formula": f"A{i + 2}",
            }
        cells[("B", length, 0)] = {
            "value_type": "number",
            "value": terminal,
            "formula": None,
        }
        return cells

    # A short chain computes normally.
    assert FormulaEvaluator(_resolver(chain(5))).evaluate_cell("B", 0, 0, "A2") == 1.0

    # A chain beyond MAX_CALL_DEPTH terminates with #CYCLE! instead of
    # recursing until the interpreter gives up — the depth bound works.
    value = FormulaEvaluator(
        _resolver(chain(MAX_CALL_DEPTH + 5)), dependencies=MAX_DEPENDENCIES
    ).evaluate_cell("B", 0, 0, "A2")
    assert value == "#CYCLE!"


def test_compute_value_entry_point():
    from src.core.suite.sum.formulas import compute_value

    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 6, "formula": None},
    }
    resolver = _resolver(cells)

    assert compute_value("formula", None, "A1*7", resolver, sheet="1") == 42.0
    assert compute_value("number", 3, None, resolver) == 3


def test_vlookup_exact_and_range_lookup():
    cells = {
        ("1", 0, 0): {"value_type": "text", "value": "apple", "formula": None},
        ("1", 0, 1): {"value_type": "number", "value": 3, "formula": None},
        ("1", 1, 0): {"value_type": "text", "value": "banana", "formula": None},
        ("1", 1, 1): {"value_type": "number", "value": 5, "formula": None},
        ("1", 2, 0): {"value_type": "text", "value": "cherry", "formula": None},
        ("1", 2, 1): {"value_type": "number", "value": 8, "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval('VLOOKUP("banana", A1:B3, 2, FALSE)', resolver) == 5.0
    assert _eval('VLOOKUP("kiwi", A1:B3, 2, FALSE)', resolver) == "#N/A"
    assert _eval('VLOOKUP("apple", A1:B3, 5, FALSE)', resolver) == "#REF!"
    # Approximate match on sorted numeric keys picks the greatest key <= target.
    numeric = {
        ("1", 0, 0): {"value_type": "number", "value": 1, "formula": None},
        ("1", 0, 1): {"value_type": "text", "value": "one", "formula": None},
        ("1", 1, 0): {"value_type": "number", "value": 10, "formula": None},
        ("1", 1, 1): {"value_type": "text", "value": "ten", "formula": None},
        ("1", 2, 0): {"value_type": "number", "value": 100, "formula": None},
        ("1", 2, 1): {"value_type": "text", "value": "hundred", "formula": None},
    }
    assert _eval("VLOOKUP(42, A1:B3, 2)", _resolver(numeric)) == "ten"
    assert _eval("VLOOKUP(500, A1:B3, 2)", _resolver(numeric)) == "hundred"
    assert _eval("VLOOKUP(-5, A1:B3, 2)", _resolver(numeric)) == "#N/A"


def test_hlookup():
    cells = {
        ("1", 0, 0): {"value_type": "text", "value": "Q1", "formula": None},
        ("1", 0, 1): {"value_type": "text", "value": "Q2", "formula": None},
        ("1", 1, 0): {"value_type": "number", "value": 100, "formula": None},
        ("1", 1, 1): {"value_type": "number", "value": 200, "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval('HLOOKUP("Q2", A1:B2, 2, FALSE)', resolver) == 200.0
    assert _eval('HLOOKUP("Q3", A1:B2, 2, FALSE)', resolver) == "#N/A"
    assert _eval('HLOOKUP("Q1", A1:B2, 5, FALSE)', resolver) == "#REF!"


def test_sumif_countif_averageif():
    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 1, "formula": None},
        ("1", 1, 0): {"value_type": "number", "value": 5, "formula": None},
        ("1", 2, 0): {"value_type": "number", "value": 10, "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval('SUMIF(A1:A3, ">2")', resolver) == 15.0
    assert _eval('COUNTIF(A1:A3, ">2")', resolver) == 2.0
    assert _eval('AVERAGEIF(A1:A3, ">2")', resolver) == 7.5
    assert _eval('SUMIF(A1:A3, ">100")', resolver) == 0.0
    assert _eval('AVERAGEIF(A1:A3, ">100")', resolver) == "#DIV/0!"
    assert _eval('COUNTIF(A1:A3, "=5")', resolver) == 1.0


def test_sumif_with_sum_range():
    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 1, "formula": None},
        ("1", 0, 1): {"value_type": "number", "value": 10, "formula": None},
        ("1", 1, 0): {"value_type": "number", "value": 5, "formula": None},
        ("1", 1, 1): {"value_type": "number", "value": 50, "formula": None},
        ("1", 2, 0): {"value_type": "number", "value": 10, "formula": None},
        ("1", 2, 1): {"value_type": "number", "value": 100, "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval('SUMIF(A1:A3, ">2", B1:B3)', resolver) == 150.0
    # Non-numeric entries in the summed range are treated as zero.
    mixed = {
        ("1", 0, 0): {"value_type": "number", "value": 5, "formula": None},
        ("1", 0, 1): {"value_type": "number", "value": 2, "formula": None},
        ("1", 1, 0): {"value_type": "number", "value": 5, "formula": None},
        ("1", 1, 1): {"value_type": "text", "value": "skip", "formula": None},
    }
    assert _eval('SUMIF(A1:A2, ">4", B1:B2)', _resolver(mixed)) == 2.0


def test_iferror_and_ifna():
    assert _eval("IFERROR(1/0, 42)") == 42.0
    assert _eval("IFERROR(2*3, 0)") == 6.0
    assert _eval('IFERROR(VLOOKUP(99, A1:A2, 1, FALSE), "missing")') == "missing"
    assert _eval('IFNA(VLOOKUP(99, A1:A2, 1, FALSE), "none")') == "none"
    # IFNA passes through non-#N/A errors unchanged.
    assert _eval("IFNA(1/0, 7)") == "#DIV/0!"


def test_date_functions():
    epoch = datetime.date(1899, 12, 30)
    assert _eval("DATE(2024, 1, 1)") == float((datetime.date(2024, 1, 1) - epoch).days)
    assert _eval("YEAR(DATE(2024, 6, 15))") == 2024.0
    assert _eval("MONTH(DATE(2024, 6, 15))") == 6.0
    assert _eval("DAY(DATE(2024, 6, 15))") == 15.0
    assert _eval("DAYS(DATE(2024, 1, 10), DATE(2024, 1, 1))") == 9.0
    # Excel-style month overflow normalizes into the following year.
    assert _eval("DATE(2024, 13, 1)") == _eval("DATE(2025, 1, 1)")
    # Date serials round-trip.
    assert _eval("DAY(0)") == 30.0
    assert _eval("MONTH(0)") == 12.0
    assert _eval("YEAR(0)") == 1899.0


def test_weeknum_honors_return_type():
    """WEEKNUM must not discard its optional ``return_type``.

    Every return type except 21 numbers from the week containing January 1 of
    the date's own year, so only the first day of the week changes between
    them. Accepting the argument and then ignoring it made the Sunday-first
    default behave like ISO-8601. A serial outside the calendar range must
    also surface as #NUM! like its neighbours DATE/YEAR, rather than raising
    an OverflowError out of the evaluator and past its error-string contract.
    """
    assert _eval("WEEKNUM(DATE(2024, 1, 1))") == 1.0
    assert _eval("WEEKNUM(DATE(2024, 1, 7))") == 2.0
    assert _eval("WEEKNUM(DATE(2024, 1, 7), 1)") == 2.0
    assert _eval("WEEKNUM(DATE(2024, 1, 8), 1)") == 2.0
    assert _eval("WEEKNUM(DATE(2024, 1, 17))") == 3.0
    assert _eval("WEEKNUM(DATE(2023, 12, 31), 1)") == 53.0
    # Type 2 (and its synonym 11) begins the week on Monday, type 12 on Tuesday.
    assert _eval("WEEKNUM(DATE(2024, 1, 7), 2)") == 1.0
    assert _eval("WEEKNUM(DATE(2024, 1, 7), 11)") == 1.0
    assert _eval("WEEKNUM(DATE(2024, 1, 7), 12)") == 2.0
    # Type 21 is ISO-8601: week 1 holds the year's first Thursday.
    assert _eval("WEEKNUM(DATE(2024, 1, 7), 21)") == 1.0
    assert _eval("WEEKNUM(DATE(2024, 1, 15), 21)") == 3.0
    assert _eval("WEEKNUM(DATE(2024, 1, 15), 4)") == NUM_ERROR
    assert _eval("WEEKNUM(1000000000)") == NUM_ERROR


def test_text_functions_expanded():
    assert _eval('SUBSTITUTE("hello world", "l", "L")') == "heLLo worLd"
    assert _eval('SUBSTITUTE("hello world", "l", "L", 2)') == "helLo world"
    assert _eval('SUBSTITUTE("hello world", "l", "L", 9)') == "hello world"
    assert _eval('EXACT("abc", "abc")') is True
    assert _eval('EXACT("abc", "ABC")') is False
    assert _eval('PROPER("hello world")') == "Hello World"


def test_left_right_mid_reject_negative_num_chars():
    """LEFT/RIGHT/MID must not silently clamp a negative ``num_chars``.

    Excel treats a negative count as #VALUE!. Clamping it to zero sliced
    from the wrong end (``LEFT("hello", -1)`` returned ``"hell"``) and hid
    the caller's mistake behind a plausible-looking result. ``num_chars`` is
    also optional for LEFT/RIGHT, where Excel defaults it to 1.
    """
    assert _eval('LEFT("hello", 2)') == "he"
    assert _eval('RIGHT("hello", 2)') == "lo"
    assert _eval('MID("hello", 2, 3)') == "ell"
    # num_chars omitted -> Excel's default of a single character.
    assert _eval('LEFT("hello")') == "h"
    assert _eval('RIGHT("hello")') == "o"
    # Zero is legal and yields the empty string. RIGHT needs its own branch:
    # ``text[-0:]`` is the whole string.
    assert _eval('LEFT("hello", 0)') == ""
    assert _eval('RIGHT("hello", 0)') == ""
    assert _eval('LEFT("hello", -1)') == VALUE_ERROR
    assert _eval('RIGHT("hello", -1)') == VALUE_ERROR
    assert _eval('MID("hello", 2, -1)') == VALUE_ERROR
    # MID's start_num is 1-based, and reading past the end is not an error.
    assert _eval('MID("hello", 0, 2)') == VALUE_ERROR
    assert _eval('MID("hello", 4, 99)') == "lo"


def test_compute_value_cache_key_uses_formula_text():
    from src.core.suite.sum.formulas import compute_value

    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 6, "formula": None},
    }
    resolver = _resolver(cells)
    # The cache key includes the formula text, so a reused evaluator with a
    # different formula on the same coordinates must not serve stale values.
    assert compute_value("formula", None, "A1+1", resolver, sheet="1") == 7.0


def test_round_exponent_is_clamped_to_spreadsheet_precision():
    """ROUND/ROUNDUP/ROUNDDOWN clamp the digit count to ±15.

    A pathological exponent (e.g. ``ROUND(1, 99999999999)``) used to compute
    ``10 ** digits`` as a multi-gigabyte big integer and stall/OOM the
    viewport read. The digit count must now clamp to the spreadsheet
    precision bound and return instantly.
    """
    assert _eval("ROUND(1, 99999999999)") == 1.0
    assert _eval("ROUNDUP(1, 99999999999)") == 1.0
    assert _eval("ROUNDDOWN(1, 99999999999)") == 1.0
    # A huge negative exponent clamps to -15 digits and rounds to zero.
    assert _eval("ROUND(1.5, -99999999999)") == 0.0
    # Precision inside the ±15 window is unchanged.
    assert _eval("ROUND(1.2345, 2)") == 1.23
    assert _eval("ROUND(123.456, -2)") == 100.0


def test_round_family_breaks_ties_away_from_zero():
    """ROUND/ROUNDUP/ROUNDDOWN follow Excel's sign rules.

    Python's ``round`` breaks ties to even and ``ceil``/``floor`` always move
    toward +Infinity, so every negative input came out a step late:
    ``ROUND(-2.5)`` gave -2 and ``ROUNDUP(-2.1)`` gave -2. Excel rounds halves
    away from zero and makes ROUNDUP/ROUNDDOWN move away from or toward zero.
    """
    assert _eval("ROUND(2.5, 0)") == 3.0
    assert _eval("ROUND(7.5, 0)") == 8.0
    assert _eval("ROUND(8.5, 0)") == 9.0
    assert _eval("ROUND(-2.5, 0)") == -3.0
    assert _eval("ROUND(-2.25, 1)") == -2.3
    # Binary-float noise must not tip a true half the wrong way.
    assert _eval("ROUND(2.675, 2)") == 2.68
    assert _eval("ROUNDUP(-2.1, 0)") == -3.0
    assert _eval("ROUNDUP(-2, 0)") == -2.0
    assert _eval("ROUNDDOWN(-2.5, 0)") == -2.0
    assert _eval("ROUNDDOWN(-2.55, 1)") == -2.5


def test_rept_is_bounded_against_multi_gb_allocations():
    """Agent 30 M3: REPT must cap the allocation like ROUND caps digits.

    ``REPT("x", 10**9)`` used to attempt a multi-gigabyte string on a read
    path; beyond the cell-length bound it now returns #VALUE!.
    """
    assert _eval('REPT("ab", 3)') == "ababab"
    assert _eval('REPT("x", 999999999)') == VALUE_ERROR


def test_networkdays_and_workday_span_is_bounded():
    """Agent 30 M3: date loops must not run for billions of iterations.

    ``NETWORKDAYS(1, 10**9)`` iterated day by day for hours and
    ``WORKDAY(1, 10**9)`` looped unbounded; both now return #NUM! once the
    requested span exceeds the evaluation budget.
    """
    start = datetime.date(2024, 1, 1)
    epoch = datetime.date(1899, 12, 30)
    serial = (start - epoch).days
    assert _eval(f"NETWORKDAYS({serial}, {serial} + 9)") == 8.0
    assert _eval("NETWORKDAYS(1, 1000000000)") == NUM_ERROR
    assert _eval(f"WORKDAY({serial}, 5)") == float(serial + 7)
    assert _eval("WORKDAY(1, 1000000000)") == NUM_ERROR


def test_networkdays_and_workday_honor_holidays_and_negative_days():
    """Both functions accepted a ``holidays`` argument but ignored it.

    A holiday must drop a weekday from NETWORKDAYS (but not twice when it
    lands on a weekend), WORKDAY must skip it while walking, a holiday range
    mixing a date, a blank, and a label must not poison the result, and a
    negative ``days`` must walk backwards instead of returning the start
    date.
    """
    epoch = datetime.date(1899, 12, 30)

    def serial(day):
        return (day - epoch).days

    jan1 = serial(datetime.date(2024, 1, 1))  # Monday
    jan3 = serial(datetime.date(2024, 1, 3))  # Wednesday
    jan6 = serial(datetime.date(2024, 1, 6))  # Saturday
    jan15 = serial(datetime.date(2024, 1, 15))  # Monday

    cells = {
        # A1:A3 = a real holiday, a blank, and a stray text label.
        ("1", 0, 0): {"value_type": "number", "value": jan3, "formula": None},
        ("1", 1, 0): {"value_type": "blank", "value": None, "formula": None},
        ("1", 2, 0): {"value_type": "text", "value": "note", "formula": None},
    }
    resolver = _resolver(cells)

    # Jan 1-7 2024 has five weekdays; the Jan 3 holiday drops one.
    assert _eval(f"NETWORKDAYS({jan1}, {jan1 + 6})", resolver) == 5.0
    assert _eval(f"NETWORKDAYS({jan1}, {jan1 + 6}, {jan3})", resolver) == 4.0
    # A holiday that lands on a weekend is already excluded.
    assert _eval(f"NETWORKDAYS({jan1}, {jan1 + 6}, {jan6})", resolver) == 5.0
    # A mixed holiday range still counts the one real date.
    assert _eval(f"NETWORKDAYS({jan1}, {jan1 + 6}, A1:A3)", resolver) == 4.0

    # WORKDAY: holidays push the result past them, negative days walk back
    # (Mon Jan 15 - 1 workday = Fri Jan 12), and zero is the start date.
    assert _eval(f"WORKDAY({jan15 - 1}, 1, {jan15})", resolver) == float(jan15 + 1)
    assert _eval(f"WORKDAY({jan15}, -1)", resolver) == float(jan15 - 3)
    assert _eval(f"WORKDAY({jan15}, 0)", resolver) == float(jan15)
