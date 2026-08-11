"""Comprehensive tests for SUBSTITUTE function."""


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
    from src.core.suite.sum.formulas import FormulaEvaluator

    return FormulaEvaluator(resolver or _grid()).evaluate_cell(sheet, row, col, formula)


def test_substitute_basic():
    """Basic substitute without instance parameter."""
    assert _eval('SUBSTITUTE("hello world", "world", "Plexi")') == "hello Plexi"
    assert _eval('SUBSTITUTE("abc", "b", "B")') == "aBc"
    assert _eval('SUBSTITUTE("aaa", "a", "b")') == "bbb"


def test_substitute_instance_1():
    """Replace only the first occurrence."""
    assert _eval('SUBSTITUTE("hello world", "l", "L", 1)') == "heLlo world"
    assert _eval('SUBSTITUTE("abcabc", "a", "X", 1)') == "Xbcabc"
    assert _eval('SUBSTITUTE("aaa", "a", "b", 1)') == "baa"


def test_substitute_instance_2():
    """Replace only the second occurrence."""
    assert _eval('SUBSTITUTE("hello world", "l", "L", 2)') == "helLo world"
    assert _eval('SUBSTITUTE("abcabc", "a", "X", 2)') == "abcbcX"
    assert _eval('SUBSTITUTE("aaa", "a", "b", 2)') == "aba"


def test_substitute_instance_3():
    """Replace only the third occurrence."""
    assert _eval('SUBSTITUTE("hello world", "l", "L", 3)') == "hello worLd"
    assert _eval('SUBSTITUTE("aaa", "a", "b", 3)') == "aab"


def test_substitute_instance_out_of_range():
    """Instance greater than occurrences returns original."""
    assert _eval('SUBSTITUTE("hello world", "l", "L", 10)') == "hello world"
    assert _eval('SUBSTITUTE("abc", "z", "Z", 1)') == "abc"
    assert _eval('SUBSTITUTE("aaa", "a", "b", 5)') == "aaa"


def test_substitute_instance_zero():
    """Instance of 0 returns error."""
    from src.core.suite.sum.formulas import VALUE_ERROR

    assert _eval('SUBSTITUTE("hello", "l", "L", 0)') == VALUE_ERROR


def test_substitute_instance_negative():
    """Negative instance returns error."""
    from src.core.suite.sum.formulas import VALUE_ERROR

    assert _eval('SUBSTITUTE("hello", "l", "L", -1)') == VALUE_ERROR


def test_substitute_empty_old():
    """Empty old text returns original."""
    assert _eval('SUBSTITUTE("hello", "", "L")') == "hello"
    assert _eval('SUBSTITUTE("hello", "", "L", 1)') == "hello"


def test_substitute_case_sensitive():
    """SUBSTITUTE is case-sensitive."""
    assert _eval('SUBSTITUTE("Hello", "h", "H")') == "Hello"
    assert _eval('SUBSTITUTE("Hello", "H", "h")') == "hello"


def test_substitute_with_cell_references():
    """SUBSTITUTE with cell references."""
    cells = {
        ("1", 0, 0): {"value_type": "text", "value": "hello world", "formula": None},
        ("1", 0, 1): {"value_type": "text", "value": "l", "formula": None},
        ("1", 0, 2): {"value_type": "text", "value": "L", "formula": None},
    }
    resolver = _resolver(cells)
    assert _eval("SUBSTITUTE(A1, B1, C1)", resolver) == "heLLo worLd"
    assert _eval("SUBSTITUTE(A1, B1, C1, 2)", resolver) == "helLo world"


def test_substitute_with_formula_cells():
    """SUBSTITUTE with formula cells."""
    cells = {
        ("1", 0, 0): {
            "value_type": "formula",
            "value": None,
            "formula": 'CONCAT("hel", "lo")',
        },
    }
    resolver = _resolver(cells)
    assert _eval('SUBSTITUTE(A1, "l", "L")', resolver) == "heLLo"


def test_substitute_single_char():
    """SUBSTITUTE with single character old text."""
    assert _eval('SUBSTITUTE("a", "a", "b")') == "b"
    assert _eval('SUBSTITUTE("a", "a", "b", 1)') == "b"
    assert _eval('SUBSTITUTE("aa", "a", "b", 2)') == "ab"


def test_substitute_multi_char_old():
    """SUBSTITUTE with multi-character old text."""
    assert _eval('SUBSTITUTE("hello world", "lo", "LO")') == "helLO world"
    assert _eval('SUBSTITUTE("abcabc", "abc", "XYZ", 2)') == "abcXYZ"


def test_substitute_new_empty():
    """SUBSTITUTE with empty new text (deletion)."""
    assert _eval('SUBSTITUTE("hello", "l", "")') == "heo"
    assert _eval('SUBSTITUTE("hello", "l", "", 1)') == "helo"
