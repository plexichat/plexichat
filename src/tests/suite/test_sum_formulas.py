"""Unit tests for the bounded PlexiSum formula engine."""

from src.core.suite.sum.formulas import (
    FormulaEvaluator,
    MAX_CALL_DEPTH,
    MAX_DEPENDENCIES,
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
    assert _eval("ISNUMBER(A1)") is True
    assert _eval("ISTEXT(B2)") is True
    assert _eval("ISBLANK(B1)") is False


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
    # The cache key includes the formula text, so a reused evaluator with a
    # different formula on the same coordinates must not serve stale values.
    assert compute_value("formula", None, "A1+1", resolver, sheet="1") == 7.0
