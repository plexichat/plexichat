"""Unit tests for the bounded PlexiSum formula engine."""

import datetime

from src.core.suite.sum.formulas import (
    DIV_ZERO,
    FormulaEvaluator,
    MAX_CALL_DEPTH,
    MAX_DEPENDENCIES,
    NAME_ERROR,
    N_A_ERROR,
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


def test_aggregates_propagate_an_error_operand():
    """SUM/AVERAGE/MIN/MAX/PRODUCT must surface an error operand.

    These aggregates filtered out anything that was not a number, so an error
    value was dropped and the function returned a plausible but wrong total:
    ``SUM(1, 1/0)`` gave 1 instead of ``#DIV/0!``. Excel propagates the error.
    COUNT is the exception -- it counts numbers only, so an error operand is
    simply not counted -- and COUNTA counts every non-empty value, error or
    not.
    """
    assert _eval("SUM(1, 1/0)") == DIV_ZERO
    assert _eval("AVERAGE(1, 1/0)") == DIV_ZERO
    assert _eval("MIN(1, 1/0)") == DIV_ZERO
    assert _eval("MAX(1, 1/0)") == DIV_ZERO
    assert _eval("PRODUCT(1, 1/0)") == DIV_ZERO
    assert _eval("COUNT(1, 1/0)") == 1
    assert _eval("COUNTA(1, 1/0)") == 2
    # An error cell inside a referenced range propagates just the same.
    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 4, "formula": None},
        ("1", 1, 0): {
            "value_type": "formula",
            "value": None,
            "formula": "1/0",
        },
    }
    assert _eval("SUM(A1:A2)", _resolver(cells)) == DIV_ZERO
    # IFERROR can still catch the propagated error.
    assert _eval("IFERROR(SUM(1, 1/0), 7)") == 7.0


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


def test_isblank_is_true_only_for_a_genuinely_empty_cell():
    """ISBLANK("") is FALSE: empty text is a value, not a blank cell.

    Treating ``""`` as blank made a cell holding the result of
    ``=IF(1=2,"","x")`` indistinguishable from an untouched one.
    """
    assert _eval('ISBLANK("")') is False
    assert _eval("ISBLANK(B1)") is False
    assert _eval("ISBLANK(A9)") is True


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


def test_match_and_lookup_search_the_first_column_of_a_block():
    """A block is searched by its first column, the way VLOOKUP keys on one.

    Flattening the whole rectangle row-major let a value in column B match at
    a position that is not a row at all: with A1="one" and B1=10,
    MATCH(10, A1:B2, 0) answered 2, the row below the cell that holds it.
    """
    cells = {
        ("1", 0, 0): {"value_type": "text", "value": "one", "formula": None},
        ("1", 0, 1): {"value_type": "number", "value": 10, "formula": None},
        ("1", 1, 0): {"value_type": "text", "value": "two", "formula": None},
        ("1", 1, 1): {"value_type": "number", "value": 20, "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval('MATCH("one", A1:B2, 0)', resolver) == 1.0
    assert _eval('MATCH("two", A1:B2, 0)', resolver) == 2.0
    assert _eval("MATCH(10, A1:B2, 0)", resolver) == N_A_ERROR
    assert _eval("MATCH(20, A1:B2, 0)", resolver) == N_A_ERROR


def test_match_and_lookup_rank_types_instead_of_coercing_them():
    """The sorted match types used to treat text as unorderable.

    ``_to_number`` raised ValueError on the first text entry and the scan
    swallowed it, so every lookup in a text column answered the length of the
    vector and LOOKUP reported #N/A for all of them. Entries are ranked the way
    Excel orders types instead -- number, then text, then logicals.
    """
    cells = {
        ("1", 0, 0): {"value_type": "text", "value": "aaa", "formula": None},
        ("1", 1, 0): {"value_type": "text", "value": "bbb", "formula": None},
        ("1", 2, 0): {"value_type": "text", "value": "ccc", "formula": None},
    }
    resolver = _resolver(cells)
    # Sorted ascending, so the answer is the last entry <= the lookup value.
    assert _eval('MATCH("aaa", A1:A3)', resolver) == 1.0
    assert _eval('MATCH("bbb", A1:A3)', resolver) == 2.0
    assert _eval('MATCH("ccc", A1:A3)', resolver) == 3.0
    assert _eval('MATCH("zzz", A1:A3)', resolver) == 3.0
    # match_type -1 walks a descending vector, so ascending data misses it.
    assert _eval('MATCH("ccc", A1:A3, -1)', resolver) == N_A_ERROR
    assert _eval('LOOKUP("bbb", A1:A3)', resolver) == "bbb"
    assert _eval('LOOKUP("bb", A1:A3)', resolver) == "aaa"
    assert _eval('LOOKUP("zzz", A1:A3)', resolver) == "ccc"


def test_lookup_reports_a_miss_when_nothing_is_at_or_below_the_target():
    """LOOKUP(0, B1:B3) over 10,20,30 is #N/A, not the last result entry."""
    cells = {
        ("1", 0, 0): {"value_type": "text", "value": "aaa", "formula": None},
        ("1", 1, 0): {"value_type": "text", "value": "bbb", "formula": None},
        ("1", 2, 0): {"value_type": "text", "value": "ccc", "formula": None},
        ("1", 0, 1): {"value_type": "number", "value": 10, "formula": None},
        ("1", 1, 1): {"value_type": "number", "value": 20, "formula": None},
        ("1", 2, 1): {"value_type": "number", "value": 30, "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval("LOOKUP(25, B1:B3)", resolver) == 20
    assert _eval("LOOKUP(9, B1:B3)", resolver) == N_A_ERROR
    # The match position indexes the result vector, not the lookup vector.
    assert _eval("LOOKUP(20, B1:B3, A1:A3)", resolver) == "bbb"
    assert _eval('LOOKUP("bbb", A1:A3, B1:B3)', resolver) == 20


def test_index_truncates_its_offsets_and_accepts_a_zero():
    """0 means "every row"/"every column", and a fractional offset truncates.

    INDEX answered #REF! for a 0 offset, so INDEX(A1:C3, 0, 2) -- the idiom for
    handing a whole column to SUM -- was an error, and 2.7 columns read as
    out-of-range rather than as column 2.
    """
    values = {
        (0, 0): 1,
        (0, 1): 10,
        (0, 2): 100,
        (1, 0): 2,
        (1, 1): 20,
        (1, 2): 200,
        (2, 0): 3,
        (2, 1): 30,
        (2, 2): 300,
    }
    cells = {
        ("1", row, col): {"value_type": "number", "value": value, "formula": None}
        for (row, col), value in values.items()
    }
    resolver = _resolver(cells)
    assert _eval("INDEX(A1:C3, 2, 3)", resolver) == 200
    assert _eval("INDEX(A1:C3, 1.9, 2.7)", resolver) == 10
    # The whole second column, as a block SUM can add up.
    assert _eval("INDEX(A1:C3, 0, 2)", resolver) == [10, 20, 30]
    assert _eval("INDEX(A1:C3, 2, 0)", resolver) == [2, 20, 200]
    assert _eval("SUM(INDEX(A1:C3, 0, 2))", resolver) == 60.0
    assert _eval("SUM(INDEX(A1:C3, 2, 0))", resolver) == 222.0
    # A negative offset is #VALUE!, one past the block is #REF!.
    assert _eval("INDEX(A1:C3, -1, 1)", resolver) == VALUE_ERROR
    assert _eval("INDEX(A1:C3, 4, 1)", resolver) == "#REF!"
    assert _eval("INDEX(A1:C3, 1, 4)", resolver) == "#REF!"
    # A single cell is a 1x1 block, and an error offset still propagates.
    assert _eval("INDEX(B1, 1)", resolver) == 10
    assert _eval("INDEX(A1:C3, 1/0, 1)", resolver) == DIV_ZERO


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


def test_weekday_honors_every_return_type():
    """WEEKDAY must implement Excel's return types 11-17, not just 1-3.

    Types 1 and 17 are Sunday-first, 2 and 11 Monday-first, 12 Tuesday-first,
    ... 16 Saturday-first. The old switch knew only 1/2/3 and fell through to
    the raw isoweekday() (1..7 from Monday) for everything else, so type 12..16
    answered the wrong day and an unknown type was silently accepted instead of
    #NUM!.
    """
    monday = "DATE(2024, 1, 15)"
    tuesday = "DATE(2024, 1, 16)"
    sunday = "DATE(2024, 1, 21)"
    assert _eval(f"WEEKDAY({monday}, 11)") == 1.0
    assert _eval(f"WEEKDAY({sunday}, 11)") == 7.0
    assert _eval(f"WEEKDAY({monday}, 12)") == 7.0
    assert _eval(f"WEEKDAY({tuesday}, 12)") == 1.0
    assert _eval(f"WEEKDAY({monday}, 13)") == 6.0
    assert _eval(f"WEEKDAY({monday}, 16)") == 3.0
    assert _eval(f"WEEKDAY({monday}, 17)") == 2.0
    # Type 3 alone numbers 0..6 from Monday.
    assert _eval(f"WEEKDAY({monday}, 3)") == 0.0
    assert _eval(f"WEEKDAY({sunday}, 3)") == 6.0
    assert _eval(f"WEEKDAY({monday}, 0)") == NUM_ERROR
    assert _eval(f"WEEKDAY({monday}, 99)") == NUM_ERROR


def test_text_functions_expanded():
    assert _eval('SUBSTITUTE("hello world", "l", "L")') == "heLLo worLd"
    assert _eval('SUBSTITUTE("hello world", "l", "L", 2)') == "helLo world"
    assert _eval('SUBSTITUTE("hello world", "l", "L", 9)') == "hello world"
    assert _eval('EXACT("abc", "abc")') is True
    assert _eval('EXACT("abc", "ABC")') is False
    assert _eval('PROPER("hello world")') == "Hello World"
    # Excel starts a new word after any non-letter and lower-cases the rest of
    # each one, so "hello wORLD" is not "Hello WORLD".
    assert _eval('PROPER("hello wORLD")') == "Hello World"
    assert _eval('PROPER("o\'neil")') == "O'Neil"
    assert _eval('PROPER("2-cent")') == "2-Cent"


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


def test_ceiling_and_floor_follow_excel_sign_rules():
    """CEILING/FLOOR match Excel's sign table, including its #NUM! case.

    These engines had no implementation at all, and the client's plain
    ceil/floor of the quotient returned a value for a positive number with a
    negative significance (``FLOOR(2.5, -2)`` gave 4) where Excel raises
    #NUM!, and leaked binary noise into the result.
    """
    # Signs matching: the magnitude rounds away from zero.
    assert _eval("CEILING(2.5, 1)") == 3.0
    assert _eval("CEILING(-2.5, -2)") == -4.0
    assert _eval("FLOOR(3.7, 2)") == 2.0
    assert _eval("FLOOR(-2.5, -2)") == -2.0
    # Negative number with a positive significance: CEILING toward zero,
    # FLOOR away from it.
    assert _eval("CEILING(-1.5, 1)") == -1.0
    assert _eval("FLOOR(-2.5, 1)") == -3.0
    # A positive number with a negative significance is #NUM!.
    assert _eval("CEILING(3.1, -3)") == NUM_ERROR
    assert _eval("FLOOR(2.5, -2)") == NUM_ERROR
    # The multiply is snapped to spreadsheet precision.
    assert _eval("FLOOR(1.58, 0.1)") == 1.5
    assert _eval("CEILING(8.26, 0.05)") == 8.3


def test_mod_carries_the_sign_of_the_divisor():
    """MOD is n - d*INT(n/d), so the result takes the divisor's sign.

    ``math.fmod`` truncates toward zero instead, which made MOD return the
    dividend's sign: ``MOD(-7, 2)`` was -1 where Excel says 1.
    """
    assert _eval("MOD(-7, 2)") == 1.0
    assert _eval("MOD(7, -2)") == -1.0
    assert _eval("MOD(-2.5, 2)") == 1.5
    assert _eval("MOD(7, 3)") == 1.0


def test_power_and_caret_reject_non_finite_results():
    """POWER and ^ report Excel's errors instead of returning non-finite data.

    A negative base with a fractional exponent and any overflow are #NUM!.
    Zero raised to a negative power is the division by zero in 1 / 0**n, so it
    is #DIV/0! rather than #NUM!, and a text operand is #VALUE!.
    """
    assert _eval("POWER(-7, 0.5)") == NUM_ERROR
    assert _eval("(-8)^0.5") == NUM_ERROR
    assert _eval("POWER(0, -3)") == DIV_ZERO
    assert _eval("0^-1") == DIV_ZERO
    assert _eval('"x"^2') == VALUE_ERROR
    assert _eval('POWER("x", 2)') == VALUE_ERROR
    # The ordinary cases are untouched.
    assert _eval("0^0") == 1.0
    assert _eval("(-8)^3") == -512.0
    assert _eval("POWER(2, 10)") == 1024.0


def test_countblank_does_not_count_zero_cells():
    """A zero cell is not blank, so COUNTBLANK must skip it.

    Counting ``val == 0`` inflated the total: a 0 in the range was reported as
    an empty cell, which Excel does not do.
    """
    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 0, "formula": None},
        ("1", 1, 0): {"value_type": "number", "value": 5, "formula": None},
        ("1", 0, 1): {"value_type": "text", "value": "", "formula": None},
    }
    resolver = _resolver(cells)
    # A1 = 0 (not blank) and A2 = 5, so that range has nothing to count.
    assert _eval("COUNTBLANK(A1:A2)", resolver) == 0.0
    # A1:B2 adds B1 = "" and the untouched B2, which are both blank.
    assert _eval("COUNTBLANK(A1:B2)", resolver) == 2.0


def test_find_and_search_report_value_error_when_text_is_missing():
    """A failed FIND/SEARCH is #VALUE! in Excel, not #N/A.

    #N/A is reserved for a lookup that found nothing, so returning it here made
    ``IFNA`` swallow a plain "not found" and hid the mistake from callers.
    """
    assert _eval('FIND("b", "abcb")') == 2.0
    assert _eval('FIND("B", "abcb")') == VALUE_ERROR
    assert _eval('FIND("x", "abc")') == VALUE_ERROR
    assert _eval('SEARCH("B", "abcb")') == 2.0
    assert _eval('SEARCH("x", "abc")') == VALUE_ERROR


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


def test_if_and_boolean_functions_propagate_an_error_condition():
    """An error *condition* is an error, not a true branch.

    ``_truthy`` returns a plain bool, so the old ``is_error(condition)`` guard
    was dead code: IF(1/0, 1, 2) took the true branch -- a non-empty error
    string is truthy -- instead of returning #DIV/0!, and AND/OR/NOT answered
    from the same misreading.
    """
    assert _eval("IF(1/0, 1, 2)") == DIV_ZERO
    assert _eval("AND(1/0)") == DIV_ZERO
    assert _eval("AND(1/0, TRUE)") == DIV_ZERO
    assert _eval("OR(1/0)") == DIV_ZERO
    assert _eval("OR(1/0, FALSE)") == DIV_ZERO
    assert _eval("NOT(1/0)") == DIV_ZERO
    # Empty text is falsy, so an empty string and a blank cell both take the
    # else branch.
    assert _eval('IF("", 1, 2)') == 2.0
    assert _eval("IF(B9, 1, 2)") == 2.0


def test_error_operands_propagate_through_the_function_table():
    """Every value-based function surfaces an error operand.

    The coercion helpers used to swallow it instead, which is how the server
    and client engines drifted apart and away from Excel: PROPER(1/0) re-cased
    the error text into "#Div/0!", REPLACE(1/0, 1, 1, "x") concatenated it into
    "xDIV/0!", ROUND(1, 1/0) blamed the wrong operand with #VALUE!, and
    MATCH(1/0, ...) reported a lookup miss as #N/A.
    """
    assert _eval("PROPER(1/0)") == DIV_ZERO
    assert _eval('REPLACE(1/0, 1, 1, "x")') == DIV_ZERO
    assert _eval("REPT(1/0, 2)") == DIV_ZERO
    assert _eval("CONCAT(1/0)") == DIV_ZERO
    assert _eval('EXACT(1/0, "a")') == DIV_ZERO
    assert _eval('FIND("a", 1/0)') == DIV_ZERO
    assert _eval('SEARCH("a", 1/0)') == DIV_ZERO
    assert _eval("VALUE(1/0)") == DIV_ZERO
    assert _eval("ROUND(1, 1/0)") == DIV_ZERO
    assert _eval("MOD(1, 1/0)") == DIV_ZERO
    assert _eval("POWER(2, 1/0)") == DIV_ZERO
    assert _eval("FLOOR(1, 1/0)") == DIV_ZERO
    assert _eval("DATE(1/0, 1, 1)") == DIV_ZERO
    assert _eval("YEAR(1/0)") == DIV_ZERO
    assert _eval("DAYS(1/0, 1)") == DIV_ZERO
    assert _eval("EOMONTH(1/0, 1)") == DIV_ZERO
    assert _eval("EDATE(1/0, 1)") == DIV_ZERO
    assert _eval("WEEKNUM(1/0)") == DIV_ZERO
    assert _eval("SIN(1/0)") == DIV_ZERO
    assert _eval("INDEX(A1:B2, 1/0, 1)") == DIV_ZERO
    assert _eval("MATCH(1/0, A1:A3, 0)") == DIV_ZERO
    assert _eval("SUMPRODUCT(A1:B2, 1/0)") == DIV_ZERO
    # An error *criteria* propagates too, while an error cell inside a
    # criteria range only fails to match.
    assert _eval("SUMIF(A1:A3, 1/0)") == DIV_ZERO
    assert _eval("COUNTIF(A1:A3, 1/0)") == DIV_ZERO


def test_is_family_and_type_inspect_an_error_operand():
    """The IS* family and TYPE answer a question *about* the error value.

    They propagate nothing: ISNUMBER(#DIV/0!) is FALSE, and TYPE reports Excel's
    error code 16 -- checked before the text branch, because an error value is a
    string.
    """
    assert _eval("ISNUMBER(1/0)") is False
    assert _eval("ISTEXT(1/0)") is False
    assert _eval("ISBLANK(1/0)") is False
    assert _eval("ISERROR(1/0)") is True
    assert _eval("ISERROR(1)") is False
    assert _eval("ISNA(NA())") is True
    assert _eval("ISNA(1/0)") is False
    assert _eval("NA()") == N_A_ERROR
    assert _eval("TYPE(1)") == 1
    assert _eval('TYPE("a")') == 2
    assert _eval("TYPE(TRUE)") == 4
    assert _eval("TYPE(1/0)") == 16


def test_numeric_text_is_coerced_only_as_an_argument():
    """A text *argument* is coerced; a value read from a range is not.

    Excel translates a text argument into a number (``SUM("2")`` is 2) but
    never a value that came out of a referenced range (``SUM(A1)`` is 0 when A1
    holds the text "2"). Without the distinction a text-formatted number column
    either summed to a plausible-looking total it should not have (the client)
    or dropped its literal arguments on the floor (the server).
    """
    assert _eval('SUM("2")') == 2.0
    assert _eval('AVERAGE("2")') == 2.0
    assert _eval('MIN("2")') == 2.0
    assert _eval('MAX("2")') == 2.0
    assert _eval('PRODUCT("2")') == 2.0
    assert _eval('COUNT("2")') == 1.0
    # The arithmetic operators coerce it the same way.
    assert _eval('"2" + 3') == 5.0
    assert _eval('"2.5" + 0') == 2.5
    # The whole argument has to read as a number, not merely a prefix of it.
    assert _eval('SUM("2abc")') == 0.0
    assert _eval('"2abc" + 1') == VALUE_ERROR

    cells = {
        ("1", 0, 0): {"value_type": "text", "value": "7", "formula": None},
        ("1", 1, 0): {"value_type": "number", "value": 5, "formula": None},
        ("1", 0, 1): {"value_type": "boolean", "value": True, "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval("SUM(A1:A2)", resolver) == 5.0
    assert _eval("SUM(A1)", resolver) == 0.0
    assert _eval("COUNT(A1:A2)", resolver) == 1.0
    assert _eval("PRODUCT(A1:A2)", resolver) == 5.0
    # A range holding no numbers averages to #DIV/0!.
    assert _eval("AVERAGE(A1:A1)", resolver) == DIV_ZERO
    # A logical value in a range is skipped; see the README divergence list.
    assert _eval("SUM(B1:B1)", resolver) == 0.0


def test_product_of_no_numeric_values_is_zero():
    """The empty product is 1 mathematically but 0 in a spreadsheet.

    Excel returns 0 for PRODUCT() and for a range that holds no numbers, and the
    client engine agrees.
    """
    assert _eval("PRODUCT()") == 0.0
    assert _eval('PRODUCT("x")') == 0.0


def test_countblank_accepts_a_single_cell_reference():
    """COUNTBLANK(A1) reports whether that one cell is blank.

    Only ``:`` ranges reached ``_range_matrix``, so a single-cell argument
    silently returned 0 for a blank cell and for an error cell alike.
    """
    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 0, "formula": None},
        ("1", 1, 0): {"value_type": "formula", "value": None, "formula": "1/0"},
    }
    resolver = _resolver(cells)
    # A zero cell holds a value, an error cell is not blank, and an untouched
    # cell is.
    assert _eval("COUNTBLANK(A1)", resolver) == 0.0
    assert _eval("COUNTBLANK(A2)", resolver) == 0.0
    assert _eval("COUNTBLANK(A3)", resolver) == 1.0


def test_text_rounds_half_away_from_zero():
    """TEXT rounds to the requested precision, exactly as ROUND does.

    ``int(value)`` truncated, so TEXT(1234.5, "0") was "1234", and rounding with
    ``floor(v + 0.5)`` mishandled a negative half, answering "0" for -0.5 where
    Excel says "-1".
    """
    assert _eval('TEXT(1234.5, "0")') == "1235"
    assert _eval('TEXT(2.5, "0")') == "3"
    assert _eval('TEXT(-0.5, "0")') == "-1"
    assert _eval('TEXT(-0.4, "0")') == "0"
    assert _eval('TEXT(-2.5, "0")') == "-3"


def test_offset_collapses_a_single_cell_block():
    """OFFSET(A1, 0, 0) is the value in A1, not a one-item list.

    Excel and the client engine both return the referenced value; only a larger
    block stays a list of values.
    """
    assert _eval("OFFSET(A1, 0, 0)") == 10.0
    assert _eval("SUM(OFFSET(A1:B2, 0, 0))") == 35.0


def test_criteria_compare_across_types_like_excel():
    """Criteria comparisons rank types instead of coercing to a number.

    Excel ranks every value (number < text < FALSE < TRUE) and compares inside
    its rank, so a text criterion carrying an operator matches the text cells
    (``COUNTIF(range, "<>x")``) while a numeric one ignores them
    (``COUNTIF(range, ">2")``). Coercing both sides with ``_to_number`` made
    every operator-prefixed text criterion match nothing.
    """
    cells = {
        ("1", 0, 0): {"value_type": "text", "value": "one", "formula": None},
        ("1", 1, 0): {"value_type": "text", "value": "two", "formula": None},
        ("1", 2, 0): {"value_type": "text", "value": "three", "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval('COUNTIF(A1:A3, "<>x")', resolver) == 3.0
    assert _eval('COUNTIF(A1:A3, "<>two")', resolver) == 2.0
    assert _eval('COUNTIF(A1:A3, "<two")', resolver) == 2.0
    assert _eval('COUNTIF(A1:A3, ">one")', resolver) == 2.0
    # Text comparison is case-insensitive, as in Excel.
    assert _eval('COUNTIF(A1:A3, "TWO")', resolver) == 1.0
    # Every number sorts below every text value, so a numeric criterion that
    # only excludes one number still matches all three text cells.
    assert _eval('COUNTIF(A1:A3, "<>2")', resolver) == 3.0
    # ... and one that asks for a number above them matches none.
    assert _eval('SUMIF(A1:A3, ">2")', resolver) == 0.0


def test_criteria_functions_accept_a_single_cell_range():
    """COUNTIF(A1, ...) tests that one cell; a ``:`` range is not required.

    Only ``A1:B2`` refs reached ``_range_matrix``, so a single-cell criteria or
    sum range looked empty and the function answered 0 (or #DIV/0!) instead of
    testing the cell.
    """
    cells = {
        ("1", 0, 0): {"value_type": "number", "value": 5, "formula": None},
        ("1", 0, 1): {"value_type": "number", "value": 50, "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval('COUNTIF(A1, ">=1")', resolver) == 1.0
    assert _eval('COUNTIF(A1, ">9")', resolver) == 0.0
    assert _eval('SUMIF(A1, ">=1", B1)', resolver) == 50.0
    assert _eval('SUMIFS(B1, A1, ">=1")', resolver) == 50.0
    assert _eval('COUNTIFS(A1, ">=1")', resolver) == 1.0
    assert _eval('AVERAGEIFS(B1, A1, ">=1")', resolver) == 50.0


def test_a_name_shaped_like_a_cell_reference_can_still_be_called():
    """``ATAN2`` is both a legal cell reference and a function name.

    The lexer matched the reference form first, so every name shaped like
    ``ATAN2`` -- ``BIN2DEC``, ``SUMX2MY2`` -- was split into a reference plus a
    stray identifier and could never be called, reporting #VALUE! (a parse
    error) where Excel reports #NAME? for a name it does not implement.
    A name followed by "(" is now the call, which is how Excel resolves it.
    """
    assert _eval("ATAN2(1, 2)") == NAME_ERROR
    assert _eval("BIN2DEC(1010)") == NAME_ERROR
    assert (
        _eval("SUMX2MY2(A1:A3, C1:C3)") == NAME_ERROR
    )  # A name that reads as a reference is still one when it is not being called:
    # "ATAN2" is column ATAN (31135 zero-based), row 2.
    cells = {("1", 1, 31135): {"value_type": "number", "value": 99, "formula": None}}
    assert _eval("ATAN2", _resolver(cells)) == 99.0


def test_an_unknown_name_is_a_name_error():
    """An unrecognised name is #NAME?, and only a malformed one is #VALUE!.

    A dot in a name used to stop the lexer, so ``FOO.BAR(1)`` raised inside the
    tokenizer and surfaced as #VALUE! -- the engine's code for a formula it
    cannot parse -- while the client engine answered #NAME?.
    """
    assert _eval("FOO.BAR(1)") == NAME_ERROR
    assert _eval("ZZZ(1)") == NAME_ERROR
    assert _eval("A1.B2") == VALUE_ERROR
