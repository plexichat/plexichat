"""Bounded formula evaluator for PlexiSum.

Formulas are stored as raw text (after the ``=``) in ``sum_cells.formula``
and evaluated on read against a resolver supplied by the caller. The engine
supports arithmetic, comparisons, string concatenation, cell references,
ranges, cross-sheet references, and a compact set of common functions.

Every evaluation is bounded: a maximum dependency count, a maximum call
depth, and cycle detection guarantee a single malicious or accidental formula
cannot stall a viewport read. Errors propagate as ``#...`` strings.

The engine is deliberately dependency-free and deterministic so it can be
unit-tested in isolation and reused by viewport reads, CSV export, and sort.
"""

import datetime
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

# Sentinel error codes returned (not raised) to callers as strings.
DIV_ZERO = "#DIV/0!"
VALUE_ERROR = "#VALUE!"
REF_ERROR = "#REF!"
NAME_ERROR = "#NAME?"
NUM_ERROR = "#NUM!"
CYCLE_ERROR = "#CYCLE!"
N_A_ERROR = "#N/A"
ERROR_CODES = (
    DIV_ZERO,
    VALUE_ERROR,
    REF_ERROR,
    NAME_ERROR,
    NUM_ERROR,
    CYCLE_ERROR,
    N_A_ERROR,
)

MAX_DEPENDENCIES = 200_000
MAX_CALL_DEPTH = 100

# Excel's epoch: serial 1 == 1900-01-01, with the classic 1900 leap-year bug
# folded into 1899-12-30 (the convention most spreadsheet engines follow).
# Dates are stored and evaluated as serial numbers.
_DATE_EPOCH = datetime.date(1899, 12, 30)

# A resolver returns the raw cell as a dict with at least
# ``value_type`` and ``value`` (and optionally ``formula``), or ``None``.
CellResolver = Callable[[Optional[str], int, int], Optional[Dict[str, Any]]]


def is_error(value: Any) -> bool:
    return isinstance(value, str) and value in ERROR_CODES


# ── tokenizer ────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"]|"")*")
  |    (?P<ref>(?:(?:'[^']*'|[A-Za-z_][A-Za-z0-9_]*)!)?\$?[A-Za-z]+\$?\d+(?::(?:(?:'[^']*'|[A-Za-z_][A-Za-z0-9_]*)!)?\$?[A-Za-z]+\$?\d+)?)
  | (?P<op><=|>=|<>|=|<|>|\+|-|\*|/|\^|&|%)
  | (?P<lparen>\()
  | (?P<rparen>\))
  | (?P<comma>,)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)


class _Token:
    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value: Any) -> None:
        self.kind = kind
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Token({self.kind}, {self.value!r})"


def _tokenize(text: str) -> List[_Token]:
    tokens: List[_Token] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise ValueError(f"Unexpected character at position {pos}")
        pos = match.end()
        kind = next(
            (name for name, value in match.groupdict().items() if value is not None),
            None,
        )
        if kind == "ws":
            continue
        raw = match.group()
        if kind == "num":
            tokens.append(_Token("num", float(raw)))
        elif kind == "str":
            tokens.append(_Token("str", raw[1:-1].replace('""', '"')))
        elif kind == "ref":
            tokens.append(_Token("ref", raw))
        elif kind == "id":
            upper = raw.upper()
            if upper == "TRUE":
                tokens.append(_Token("bool", True))
            elif upper == "FALSE":
                tokens.append(_Token("bool", False))
            else:
                tokens.append(_Token("id", upper))
        elif kind == "lparen":
            tokens.append(_Token("lparen", "("))
        elif kind == "rparen":
            tokens.append(_Token("rparen", ")"))
        elif kind == "comma":
            tokens.append(_Token("comma", ","))
        else:
            tokens.append(_Token("op", raw))
    tokens.append(_Token("eof", None))
    return tokens


# ── cell reference parsing ───────────────────────────────────────────────────

_CELL_RE = re.compile(
    r"^(?:(?:'([^']*)'|([A-Za-z_][A-Za-z0-9_]*))!)?\$?([A-Za-z]+)\$?(\d+)$"
)

# Named range pattern (e.g., "MyRange", "Sheet1!MyRange")
_NAMED_RANGE_RE = re.compile(
    r"^(?:(?:'([^']*)'|([A-Za-z_][A-Za-z0-9_]*))!)?([A-Za-z_][A-Za-z0-9_]*)$"
)


def parse_cell_ref(
    text: str, named_ranges: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], int, int]:
    """Parse ``Sheet2!A1`` or ``'Sheet 1'!A1`` -> (sheet_name, row, col).

    Absolute ``$`` markers are ignored, and single-quoted sheet names (the
    Excel convention for names containing spaces or punctuation, e.g. the
    default ``Sheet 1``) are accepted.

    If *named_ranges* is provided and the text matches a named range,
    the range's resolved reference is returned instead.
    """
    # First, try to match as a cell reference
    match = _CELL_RE.match(text)
    if match:
        quoted, unquoted, letters, row_text = match.groups()
        sheet = quoted if quoted is not None else unquoted
        column = 0
        for char in letters.upper():
            column = column * 26 + (ord(char) - 64)
        return sheet, int(row_text) - 1, column - 1
    # Try to match as a named range
    if named_ranges:
        named_match = _NAMED_RANGE_RE.match(text)
        if named_match:
            quoted, unquoted, name = named_match.groups()
            sheet = quoted if quoted is not None else unquoted
            # Look up the named range
            range_key = name.upper()
            if range_key in named_ranges:
                range_ref = named_ranges[range_key]
                if isinstance(range_ref, str):
                    # Recursive call to parse the range reference
                    return parse_cell_ref(range_ref, named_ranges)
    raise ValueError(f"Invalid cell reference {text!r}")


# ── parser ───────────────────────────────────────────────────────────────────


class _Parser:
    def __init__(self, tokens: List[_Token]) -> None:
        self._tokens = tokens
        self._index = 0

    def _peek(self) -> _Token:
        return self._tokens[self._index]

    def _advance(self) -> _Token:
        token = self._tokens[self._index]
        if token.kind != "eof":
            self._index += 1
        return token

    def _expect_op(self, op: str) -> None:
        # Matching by value (not kind) so ``(`` works both as a grouping
        # parenthesis and as a function-call delimiter.
        token = self._peek()
        if token.value != op:
            raise ValueError(f"Expected {op!r}")

    def parse(self) -> Any:
        node = self._expr()
        if self._peek().kind != "eof":
            raise ValueError("Unexpected trailing input")
        return node

    def _expr(self) -> Any:
        # comparison / equality (lowest precedence)
        node = self._additive()
        while self._peek().kind == "op" and self._peek().value in (
            "<",
            ">",
            "<=",
            ">=",
            "=",
            "<>",
        ):
            op = self._advance().value
            right = self._additive()
            node = ("cmp", op, node, right)
        return node

    def _additive(self) -> Any:
        node = self._multiplicative()
        while self._peek().kind == "op" and self._peek().value in ("+", "-", "&"):
            op = self._advance().value
            right = self._multiplicative()
            node = (op, node, right)
        return node

    def _multiplicative(self) -> Any:
        node = self._power()
        while self._peek().kind == "op" and self._peek().value in ("*", "/"):
            op = self._advance().value
            right = self._power()
            node = (op, node, right)
        return node

    def _power(self) -> Any:
        node = self._unary()
        if self._peek().kind == "op" and self._peek().value == "^":
            self._advance()
            right = self._power()
            node = ("^", node, right)
        return node

    def _unary(self) -> Any:
        token = self._peek()
        if token.kind == "op" and token.value in ("+", "-"):
            self._advance()
            operand = self._unary()
            return ("neg", operand) if token.value == "-" else operand
        node = self._postfix()
        if self._peek().kind == "op" and self._peek().value == "%":
            self._advance()
            return ("%", node)
        return node

    def _postfix(self) -> Any:
        token = self._peek()
        if token.kind == "num":
            self._advance()
            return ("num", token.value)
        if token.kind == "str":
            self._advance()
            return ("str", token.value)
        if token.kind == "bool":
            self._advance()
            return ("bool", token.value)
        if token.kind == "ref":
            self._advance()
            return ("ref", token.value)
        if token.kind == "id":
            self._advance()
            self._expect_op("(")
            self._advance()
            args: List[Any] = []
            if self._peek().kind != "rparen":
                while True:
                    args.append(self._expr())
                    if self._peek().kind == "comma":
                        self._advance()
                        continue
                    break
            self._expect_op(")")
            self._advance()
            return ("call", token.value, args)
        if token.kind == "lparen":
            self._advance()
            node = self._expr()
            self._expect_op(")")
            self._advance()
            return node
        raise ValueError(f"Unexpected token {token!r}")


def parse_formula(text: str) -> Any:
    return _Parser(_tokenize(text)).parse()


# ── evaluation ───────────────────────────────────────────────────────────────


def _to_number(value: Any) -> float:
    if value is None:
        # Blank cells coerce to zero in arithmetic (Excel behavior); only
        # text that cannot be parsed stays a #VALUE! error.
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            raise ValueError(VALUE_ERROR)
    raise ValueError(VALUE_ERROR)


def _to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_number_or_zero(value: Any) -> float:
    """Numeric coercion that treats blanks and non-numeric text as zero.

    Used by criteria functions (SUMIF/AVERAGEIF) where Excel silently
    ignores non-numeric entries in the summed range.
    """
    if value is None:
        return 0.0
    try:
        return _to_number(value)
    except ValueError:
        return 0.0


class FormulaEvaluator:
    """Evaluate parsed formula ASTs against a resolver with hard bounds."""

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
            raise ValueError(NUM_ERROR)
        seen.add(key)
        try:
            node = parse_formula(formula)
            value = self._eval(node, sheet, seen, depth)
            self._computed[key] = value
            return value
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
            raise ValueError(NUM_ERROR)
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
                    raise ValueError(NUM_ERROR)
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
        if kind == "ref":
            ref = node[1]
            r_sheet, row, column = parse_cell_ref(ref, self._named_ranges)
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
                    comparison = _to_text(left) == _to_text(right)
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
            if isinstance(left, str) or isinstance(right, str):
                return VALUE_ERROR
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
                return math.pow(_to_number(left), _to_number(right))
            except (ValueError, OverflowError):
                return NUM_ERROR
        return VALUE_ERROR

    def _call(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        # Evaluate the arguments lazily where the function is short-circuiting.
        if name == "IF":
            if len(args) != 3:
                return VALUE_ERROR
            condition = self._truthy(self._eval(args[0], sheet, seen, depth))
            if (
                is_error(condition)
                and isinstance(condition, str)
                and condition in ERROR_CODES
            ):
                return condition
            branch = args[1] if condition else args[2]
            return self._eval(branch, sheet, seen, depth)
        if name in ("AND", "OR"):
            if not args:
                return VALUE_ERROR
            results = [
                self._truthy(self._eval(arg, sheet, seen, depth)) for arg in args
            ]
            if any(is_error(r) for r in results):
                return VALUE_ERROR
            return all(results) if name == "AND" else any(results)
        if name == "NOT":
            if len(args) != 1:
                return VALUE_ERROR
            return not self._truthy(self._eval(args[0], sheet, seen, depth))
        if name in ("VLOOKUP", "HLOOKUP"):
            return self._lookup(name, args, sheet, seen, depth)
        if name in ("SUMIF", "COUNTIF", "AVERAGEIF"):
            return self._criteria(name, args, sheet, seen, depth)
        if name in ("IFERROR", "IFNA"):
            if len(args) != 2:
                return VALUE_ERROR
            value = self._eval(args[0], sheet, seen, depth)
            if isinstance(value, str) and value in ERROR_CODES:
                if name == "IFNA" and value != N_A_ERROR:
                    return value
                return self._eval(args[1], sheet, seen, depth)
            return value

        values: List[Any] = []
        for arg in args:
            values.extend(self._expand_arg(arg, sheet, seen, depth))
        if name in ("SUM", "AVERAGE", "COUNT", "COUNTA", "MIN", "MAX", "PRODUCT"):
            return self._aggregate(name, values)
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
        if name in (
            "ROUND",
            "ROUNDUP",
            "ROUNDDOWN",
            "MOD",
            "POWER",
            "LEFT",
            "RIGHT",
            "MID",
        ):
            return self._double(name, values)
        if name in ("CONCAT", "CONCATENATE"):
            return "".join(_to_text(value) for value in values)
        if name == "EXACT":
            if len(values) != 2:
                return VALUE_ERROR
            return _to_text(values[0]) == _to_text(values[1])
        if name == "PROPER":
            if len(values) != 1:
                return VALUE_ERROR
            text = _to_text(values[0])
            return "".join(
                part[:1].upper() + part[1:].lower() for part in re.split(r"(\s+)", text)
            )
        if name == "SUBSTITUTE":
            return self._substitute(values)
        if name in ("DATE", "YEAR", "MONTH", "DAY"):
            return self._date(name, values)
        if name == "DAYS":
            return self._days(values)
        # ── Advanced functions ──────────────────────────────────────────────
        if name in ("INDEX", "MATCH", "INDIRECT", "OFFSET", "LOOKUP"):
            return self._lookup_advanced(name, args, sheet, seen, depth)
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
        ):
            return self._advanced_text(name, values)
        if name in ("EOMONTH", "EDATE", "WEEKDAY", "WEEKNUM", "NETWORKDAYS", "WORKDAY"):
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
    ) -> List[Any]:
        kind = node[0]
        if kind == "ref":
            text = node[1]
            if ":" in text:
                first, second = text.split(":", 1)
                s1, r1, c1 = parse_cell_ref(first, self._named_ranges)
                s2, r2, c2 = parse_cell_ref(second, self._named_ranges)
                return self._range_values(s1 or sheet, r1, c1, r2, c2, seen, depth)
            return [self._eval(node, sheet, seen, depth)]
        value = self._eval(node, sheet, seen, depth)
        return [value]

    def _truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if _numeric(value):
            return value != 0
        if isinstance(value, str) and value not in ERROR_CODES:
            return True
        return bool(value)

    def _aggregate(self, name: str, values: List[Any]) -> Any:
        if name == "COUNTA":
            return sum(1 for value in values if value is not None and value != "")
        if name == "COUNT":
            return sum(1 for value in values if _numeric(value))
        numbers = [value for value in values if _numeric(value)]
        if name == "SUM":
            return float(sum(numbers))
        if name == "PRODUCT":
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
        if not values:
            return VALUE_ERROR
        value = values[0]
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
            return float(len(_to_text(value)))
        if name == "UPPER":
            return _to_text(value).upper()
        if name == "LOWER":
            return _to_text(value).lower()
        if name == "TRIM":
            return _to_text(value).strip()
        if name == "ISBLANK":
            return value is None or value == ""
        if name == "ISNUMBER":
            return _numeric(value)
        if name == "ISTEXT":
            return isinstance(value, str) and value not in ERROR_CODES
        return VALUE_ERROR

    def _double(self, name: str, values: List[Any]) -> Any:
        if len(values) < 2:
            return VALUE_ERROR
        a, b = values[0], values[1]
        if is_error(a):
            return a
        if name == "MOD":
            try:
                divisor = _to_number(b)
            except ValueError as exc:
                return str(exc)
            if divisor == 0:
                return DIV_ZERO
            return math.fmod(_to_number(a), divisor)
        if name == "POWER":
            try:
                return math.pow(_to_number(a), _to_number(b))
            except (ValueError, OverflowError):
                return NUM_ERROR
        if name in ("ROUND", "ROUNDUP", "ROUNDDOWN"):
            digits = int(_to_number(b))
            factor = 10**digits
            number = _to_number(a)
            if name == "ROUND":
                return round(number * factor) / factor
            if name == "ROUNDUP":
                return math.ceil(number * factor - 1e-12) / factor
            return math.floor(number * factor + 1e-12) / factor
        if name == "LEFT":
            return _to_text(a)[: max(0, int(_to_number(b)))]
        if name == "RIGHT":
            count = max(0, int(_to_number(b)))
            text = _to_text(a)
            return text[-count:] if count else ""
        if name == "MID":
            if len(values) < 3:
                return VALUE_ERROR
            start = int(_to_number(b)) - 1
            length = max(0, int(_to_number(values[2])))
            text = _to_text(a)
            return text[start : start + length]
        return VALUE_ERROR

    def _range_matrix(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> List[List[Any]]:
        """Resolve a ``A1:C10`` ref node into a 2-D matrix of values.

        Lookup and criteria functions need the *shape* of a range (rows ×
        columns), which the flat ``_expand_arg`` path discards.
        """
        if node[0] != "ref" or ":" not in node[1]:
            return []
        first, second = node[1].split(":", 1)
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

    @staticmethod
    def _lookup_eq(a: Any, b: Any) -> bool:
        """Equality used by V/HLOOKUP: numeric cells compare numerically,
        anything text-like compares as text."""
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        if isinstance(a, str) or isinstance(b, str):
            return _to_text(a) == _to_text(b)
        try:
            return _to_number(a) == _to_number(b)
        except ValueError:
            return _to_text(a) == _to_text(b)

    def _lookup(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """VLOOKUP / HLOOKUP with exact or sorted approximate matching."""
        if len(args) < 3 or len(args) > 4:
            return VALUE_ERROR
        lookup_value = self._eval(args[0], sheet, seen, depth)
        if is_error(lookup_value):
            return lookup_value
        matrix = self._range_matrix(args[1], sheet, seen, depth)
        if not matrix:
            return N_A_ERROR
        try:
            index = int(_to_number(self._eval(args[2], sheet, seen, depth))) - 1
        except ValueError as exc:
            return str(exc)
        if index < 0:
            return VALUE_ERROR
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
                if self._lookup_eq(key, lookup_value):
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
            if self._lookup_eq(row[0], lookup_value):
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
        matrix = self._range_matrix(args[0], sheet, seen, depth)
        if not matrix:
            return DIV_ZERO if name == "AVERAGEIF" else 0.0
        first = args[1]
        if first[0] == "str":
            criteria: Any = first[1]
        elif first[0] == "num":
            criteria = first[1]
        else:
            criteria = self._eval(first, sheet, seen, depth)
        sum_matrix = matrix
        if len(args) == 3:
            sum_matrix = self._range_matrix(args[2], sheet, seen, depth)
        total = 0.0
        count = 0
        for r in range(min(len(matrix), len(sum_matrix))):
            row = matrix[r]
            sum_row = sum_matrix[r]
            for c in range(min(len(row), len(sum_row))):
                if self._criteria_test(criteria, row[c]):
                    total += _to_number_or_zero(sum_row[c])
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
        """Match ``value`` against an Excel-style criteria (e.g. ``">2"``)."""
        if criteria is None:
            return value is None or value == ""
        if isinstance(criteria, str):
            for op in (">=", "<=", "<>", ">", "<", "="):
                if criteria.startswith(op) and len(criteria) > len(op):
                    rest = criteria[len(op) :].strip()
                    try:
                        target = float(rest)
                    except ValueError:
                        return op == "=" and _to_text(value) == rest
                    try:
                        number = _to_number(value)
                    except ValueError:
                        return False
                    if op == ">":
                        return number > target
                    if op == "<":
                        return number < target
                    if op == ">=":
                        return number >= target
                    if op == "<=":
                        return number <= target
                    if op == "<>":
                        return number != target
                    return number == target
            return _to_text(value) == criteria
        if isinstance(value, str) or isinstance(value, bool):
            return value == criteria
        try:
            return _to_number(value) == _to_number(criteria)
        except ValueError:
            return False

    def _substitute(self, values: List[Any]) -> Any:
        """SUBSTITUTE(text, old, new, [instance]).

        When *instance* is provided, only that specific occurrence of *old*
        is replaced (1-based).

        Example: SUBSTITUTE("hello world", "l", "L", 2)
          - Finds the 2nd 'l' at position 3
          - Replaces it with 'L'
          - Result: "helLo world"
        """
        if len(values) < 3 or len(values) > 4:
            return VALUE_ERROR
        text = _to_text(values[0])
        old = _to_text(values[1])
        new = _to_text(values[2])
        if old == "":
            return text
        if len(values) == 4:
            try:
                instance = int(_to_number(values[3]))
            except ValueError as exc:
                return str(exc)
            if instance < 1:
                return VALUE_ERROR
            # Find the position of the Nth occurrence
            start = 0
            count = 0
            while count < instance:
                pos = text.find(old, start)
                if pos == -1:
                    # Not enough occurrences; return original text
                    return text
                count += 1
                if count == instance:
                    # Found the Nth occurrence; replace it
                    return text[:pos] + new + text[pos + len(old) :]
                start = pos + 1
            return text
        return text.replace(old, new)

    def _date(self, name: str, values: List[Any]) -> Any:
        """DATE / YEAR / MONTH / DAY over Excel serial dates."""
        if name == "DATE":
            if len(values) != 3:
                return VALUE_ERROR
            try:
                year = int(_to_number(values[0]))
                month = int(_to_number(values[1]))
                day = int(_to_number(values[2]))
            except ValueError as exc:
                return str(exc)
            # Excel normalizes out-of-range months (13 -> January next year)
            # and day overflow via date arithmetic.
            total_months = year * 12 + (month - 1)
            normalized_year = total_months // 12
            normalized_month = total_months % 12 + 1
            if normalized_year < 1:
                return NUM_ERROR
            try:
                base = datetime.date(normalized_year, normalized_month, 1)
                serial = (base + datetime.timedelta(days=day - 1) - _DATE_EPOCH).days
            except (ValueError, OverflowError):
                return NUM_ERROR
            return float(serial)
        if len(values) != 1:
            return VALUE_ERROR
        try:
            serial = int(_to_number(values[0]))
        except ValueError as exc:
            return str(exc)
        try:
            date_value = _DATE_EPOCH + datetime.timedelta(days=serial)
        except (ValueError, OverflowError):
            return NUM_ERROR
        if name == "YEAR":
            return float(date_value.year)
        if name == "MONTH":
            return float(date_value.month)
        if name == "DAY":
            return float(date_value.day)
        return VALUE_ERROR

    def _days(self, values: List[Any]) -> Any:
        """DAYS(end_date, start_date) -> whole-day difference."""
        if len(values) != 2:
            return VALUE_ERROR
        try:
            end = int(_to_number(values[0]))
            start = int(_to_number(values[1]))
        except ValueError as exc:
            return str(exc)
        return float(end - start)

    # ── Advanced lookup functions ───────────────────────────────────────────

    def _lookup_advanced(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """INDEX, MATCH, INDIRECT, OFFSET, LOOKUP."""
        if name == "INDEX":
            # INDEX(array, row_num, [col_num])
            if len(args) < 2 or len(args) > 3:
                return VALUE_ERROR
            matrix = self._range_matrix(args[0], sheet, seen, depth)
            if not matrix:
                return REF_ERROR
            row_idx = int(_to_number(self._eval(args[1], sheet, seen, depth))) - 1
            col_idx = 0
            if len(args) == 3:
                col_idx = int(_to_number(self._eval(args[2], sheet, seen, depth))) - 1
            if row_idx < 0 or row_idx >= len(matrix):
                return REF_ERROR
            if col_idx < 0 or col_idx >= len(matrix[row_idx]):
                return REF_ERROR
            return matrix[row_idx][col_idx]
        elif name == "MATCH":
            # MATCH(lookup_value, lookup_array, [match_type])
            if len(args) < 2 or len(args) > 3:
                return VALUE_ERROR
            lookup_value = self._eval(args[0], sheet, seen, depth)
            matrix = self._range_matrix(args[1], sheet, seen, depth)
            if not matrix:
                return N_A_ERROR
            match_type = 1
            if len(args) == 3:
                match_type = int(_to_number(self._eval(args[2], sheet, seen, depth)))
            # Flatten matrix for matching
            flat = [row[0] if row else None for row in matrix]
            if match_type == 0:  # Exact match
                for i, val in enumerate(flat):
                    if self._lookup_eq(val, lookup_value):
                        return float(i + 1)
            elif match_type == 1:  # Less than (sorted ascending)
                for i, val in enumerate(flat):
                    try:
                        if _to_number(val) > _to_number(lookup_value):
                            return float(i) if i > 0 else N_A_ERROR
                    except ValueError:
                        continue
                return float(len(flat))
            elif match_type == -1:  # Greater than (sorted descending)
                for i, val in enumerate(flat):
                    try:
                        if _to_number(val) < _to_number(lookup_value):
                            return float(i) if i > 0 else N_A_ERROR
                    except ValueError:
                        continue
                return float(len(flat))
            return N_A_ERROR
        elif name == "INDIRECT":
            # INDIRECT(ref_text, [a1])
            if len(args) < 1 or len(args) > 2:
                return VALUE_ERROR
            ref_text = _to_text(self._eval(args[0], sheet, seen, depth))
            try:
                r_sheet, row, col = parse_cell_ref(ref_text, self._named_ranges)
                target = r_sheet or sheet
                return self._resolve(target, row, col, seen, depth)
            except ValueError:
                return REF_ERROR
        elif name == "OFFSET":
            # OFFSET(reference, rows, cols, [height], [width])
            if len(args) < 3 or len(args) > 5:
                return VALUE_ERROR
            # Get the reference range
            ref_node = args[0]
            if ref_node[0] != "ref":
                return VALUE_ERROR
            ref_text = ref_node[1]
            if ":" not in ref_text:
                # Single cell reference
                first = ref_text
                second = ref_text
            else:
                first, second = ref_text.split(":", 1)
            s1, r1, c1 = parse_cell_ref(first, self._named_ranges)
            s2, r2, c2 = parse_cell_ref(second, self._named_ranges)
            target = s1 or sheet
            rows = int(_to_number(self._eval(args[1], sheet, seen, depth)))
            cols = int(_to_number(self._eval(args[2], sheet, seen, depth)))
            height = r2 - r1 + 1
            width = c2 - c1 + 1
            if len(args) >= 4:
                height = int(_to_number(self._eval(args[3], sheet, seen, depth)))
            if len(args) == 5:
                width = int(_to_number(self._eval(args[4], sheet, seen, depth)))
            # Return the range as a flat list
            result = []
            for r in range(r1 + rows, r1 + rows + height):
                for c in range(c1 + cols, c1 + cols + width):
                    result.append(self._resolve(target, r, c, seen, depth + 1))
            return result
        elif name == "LOOKUP":
            # LOOKUP(lookup_value, lookup_vector, [result_vector])
            if len(args) < 2 or len(args) > 3:
                return VALUE_ERROR
            lookup_value = self._eval(args[0], sheet, seen, depth)
            matrix = self._range_matrix(args[1], sheet, seen, depth)
            if not matrix:
                return N_A_ERROR
            flat = [row[0] if row else None for row in matrix]
            result_flat = flat
            if len(args) == 3:
                result_matrix = self._range_matrix(args[2], sheet, seen, depth)
                result_flat = [row[0] if row else None for row in result_matrix]
            # LOOKUP assumes sorted ascending, finds last value <= lookup_value
            best_idx = -1
            for i, val in enumerate(flat):
                try:
                    if _to_number(val) <= _to_number(lookup_value):
                        best_idx = i
                except ValueError:
                    continue
            if best_idx >= 0 and best_idx < len(result_flat):
                return result_flat[best_idx]
            return N_A_ERROR
        return VALUE_ERROR

    # ── Advanced aggregate functions ────────────────────────────────────────

    def _advanced_aggregate(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """COUNTBLANK, SUMPRODUCT."""
        if name == "COUNTBLANK":
            if len(args) != 1:
                return VALUE_ERROR
            matrix = self._range_matrix(args[0], sheet, seen, depth)
            if not matrix:
                return 0.0
            count = 0
            for row in matrix:
                for val in row:
                    if val is None or val == "" or val == 0:
                        count += 1
            return float(count)
        elif name == "SUMPRODUCT":
            # SUMPRODUCT(array1, [array2], ...)
            if not args:
                return VALUE_ERROR
            matrices = []
            for arg in args:
                matrix = self._range_matrix(arg, sheet, seen, depth)
                if not matrix:
                    return VALUE_ERROR
                matrices.append(matrix)
            # All matrices must have same shape
            rows = len(matrices[0])
            cols = len(matrices[0][0]) if rows > 0 else 0
            for m in matrices:
                if len(m) != rows or (rows > 0 and len(m[0]) != cols):
                    return VALUE_ERROR
            # Calculate sumproduct
            total = 0.0
            for r in range(rows):
                for c in range(cols):
                    product = 1.0
                    for m in matrices:
                        val = m[r][c]
                        if val is None:
                            product = 0.0
                            break
                        try:
                            product *= _to_number(val)
                        except ValueError:
                            product = 0.0
                            break
                    total += product
            return total
        return VALUE_ERROR

    # ── Advanced text functions ─────────────────────────────────────────────

    def _advanced_text(self, name: str, values: List[Any]) -> Any:
        """TEXT, VALUE, NUMBERVALUE, FIND, SEARCH, REPLACE, REPT."""
        if name == "TEXT":
            # TEXT(value, format_text)
            if len(values) != 2:
                return VALUE_ERROR
            value = values[0]
            fmt = _to_text(values[1])
            if _numeric(value):
                # Simple numeric formatting
                if fmt == "0":
                    return str(int(value))
                elif fmt == "0.0":
                    return f"{value:.1f}"
                elif fmt == "0.00":
                    return f"{value:.2f}"
                elif fmt == ",":
                    return f"{int(value):,}"
                elif fmt == "$":
                    return f"${value:,.2f}"
                elif fmt == "%":
                    return f"{value * 100:.1f}%"
                elif "yyyy" in fmt.lower():
                    # Date formatting
                    try:
                        date_val = _DATE_EPOCH + datetime.timedelta(days=int(value))
                        return date_val.strftime("%Y-%m-%d")
                    except (ValueError, OverflowError):
                        return VALUE_ERROR
            return _to_text(value)
        elif name == "VALUE":
            if len(values) != 1:
                return VALUE_ERROR
            text = _to_text(values[0])
            try:
                return float(text.strip())
            except ValueError:
                return VALUE_ERROR
        elif name == "NUMBERVALUE":
            # NUMBERVALUE(text, [decimal_separator], [group_separator])
            if len(values) < 1 or len(values) > 3:
                return VALUE_ERROR
            text = _to_text(values[0])
            decimal_sep = "."
            group_sep = ","
            if len(values) >= 2:
                decimal_sep = _to_text(values[1])
            if len(values) == 3:
                group_sep = _to_text(values[2])
            # Remove group separators and replace decimal separator
            text = text.replace(group_sep, "")
            text = text.replace(decimal_sep, ".")
            try:
                return float(text.strip())
            except ValueError:
                return VALUE_ERROR
        elif name == "FIND":
            # FIND(find_text, within_text, [start_num])
            if len(values) < 2 or len(values) > 3:
                return VALUE_ERROR
            find_text = _to_text(values[0])
            within_text = _to_text(values[1])
            start_num = 1
            if len(values) == 3:
                start_num = int(_to_number(values[2]))
            if start_num < 1:
                return VALUE_ERROR
            pos = within_text.find(find_text, start_num - 1)
            if pos == -1:
                return N_A_ERROR
            return float(pos + 1)
        elif name == "SEARCH":
            # SEARCH(find_text, within_text, [start_num])
            # Case-insensitive version of FIND
            if len(values) < 2 or len(values) > 3:
                return VALUE_ERROR
            find_text = _to_text(values[0]).lower()
            within_text = _to_text(values[1]).lower()
            start_num = 1
            if len(values) == 3:
                start_num = int(_to_number(values[2]))
            if start_num < 1:
                return VALUE_ERROR
            pos = within_text.find(find_text, start_num - 1)
            if pos == -1:
                return N_A_ERROR
            return float(pos + 1)
        elif name == "REPLACE":
            # REPLACE(old_text, start_num, num_chars, new_text)
            if len(values) != 4:
                return VALUE_ERROR
            old_text = _to_text(values[0])
            start_num = int(_to_number(values[1]))
            num_chars = int(_to_number(values[2]))
            new_text = _to_text(values[3])
            if start_num < 1:
                return VALUE_ERROR
            start = start_num - 1
            return old_text[:start] + new_text + old_text[start + num_chars :]
        elif name == "REPT":
            # REPT(text, number_times)
            if len(values) != 2:
                return VALUE_ERROR
            text = _to_text(values[0])
            times = int(_to_number(values[1]))
            if times < 0:
                return VALUE_ERROR
            return text * times
        return VALUE_ERROR

    # ── Advanced date functions ─────────────────────────────────────────────

    def _advanced_date(self, name: str, values: List[Any]) -> Any:
        """EOMONTH, EDATE, WEEKDAY, WEEKNUM, NETWORKDAYS, WORKDAY."""
        if name == "EOMONTH":
            # EOMONTH(start_date, months)
            if len(values) != 2:
                return VALUE_ERROR
            try:
                start_serial = int(_to_number(values[0]))
                months = int(_to_number(values[1]))
            except ValueError:
                return VALUE_ERROR
            start_date = _DATE_EPOCH + datetime.timedelta(days=start_serial)
            # Add months
            month = start_date.month + months
            year = start_date.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            # Get last day of month
            if month == 12:
                end_date = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
            else:
                end_date = datetime.date(year, month + 1, 1) - datetime.timedelta(
                    days=1
                )
            return float((end_date - _DATE_EPOCH).days)
        elif name == "EDATE":
            # EDATE(start_date, months)
            if len(values) != 2:
                return VALUE_ERROR
            try:
                start_serial = int(_to_number(values[0]))
                months = int(_to_number(values[1]))
            except ValueError:
                return VALUE_ERROR
            start_date = _DATE_EPOCH + datetime.timedelta(days=start_serial)
            month = start_date.month + months
            year = start_date.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            day = min(
                start_date.day,
                [
                    31,
                    29
                    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
                    else 28,
                    31,
                    30,
                    31,
                    30,
                    31,
                    31,
                    30,
                    31,
                    30,
                    31,
                ][month - 1],
            )
            end_date = datetime.date(year, month, day)
            return float((end_date - _DATE_EPOCH).days)
        elif name == "WEEKDAY":
            # WEEKDAY(serial_number, [return_type])
            if len(values) < 1 or len(values) > 2:
                return VALUE_ERROR
            try:
                serial = int(_to_number(values[0]))
            except ValueError:
                return VALUE_ERROR
            return_type = 1
            if len(values) == 2:
                return_type = int(_to_number(values[1]))
            date_val = _DATE_EPOCH + datetime.timedelta(days=serial)
            weekday = date_val.isoweekday()  # 1=Monday, 7=Sunday
            if return_type == 1:  # 1=Sunday, 7=Saturday
                return float((weekday % 7) + 1)
            elif return_type == 2:  # 1=Monday, 7=Sunday
                return float(weekday)
            elif return_type == 3:  # 0=Monday, 6=Sunday
                return float(weekday - 1)
            return float(weekday)
        elif name == "WEEKNUM":
            # WEEKNUM(serial_number, [return_type])
            if len(values) < 1 or len(values) > 2:
                return VALUE_ERROR
            try:
                serial = int(_to_number(values[0]))
            except ValueError:
                return VALUE_ERROR
            date_val = _DATE_EPOCH + datetime.timedelta(days=serial)
            return float(date_val.isocalendar()[1])
        elif name == "NETWORKDAYS":
            # NETWORKDAYS(start_date, end_date, [holidays])
            if len(values) < 2 or len(values) > 3:
                return VALUE_ERROR
            try:
                start_serial = int(_to_number(values[0]))
                end_serial = int(_to_number(values[1]))
            except ValueError:
                return VALUE_ERROR
            start_date = _DATE_EPOCH + datetime.timedelta(days=start_serial)
            end_date = _DATE_EPOCH + datetime.timedelta(days=end_serial)
            count = 0
            current = start_date
            while current <= end_date:
                if current.isoweekday() <= 5:  # Monday-Friday
                    count += 1
                current += datetime.timedelta(days=1)
            return float(count)
        elif name == "WORKDAY":
            # WORKDAY(start_date, days, [holidays])
            if len(values) < 2 or len(values) > 3:
                return VALUE_ERROR
            try:
                start_serial = int(_to_number(values[0]))
                work_days = int(_to_number(values[1]))
            except ValueError:
                return VALUE_ERROR
            current = _DATE_EPOCH + datetime.timedelta(days=start_serial)
            remaining = work_days
            while remaining > 0:
                current += datetime.timedelta(days=1)
                if current.isoweekday() <= 5:  # Monday-Friday
                    remaining -= 1
            return float((current - _DATE_EPOCH).days)
        return VALUE_ERROR

    # ── Advanced trigonometric functions ─────────────────────────────────────

    def _advanced_trig(self, name: str, values: List[Any]) -> Any:
        """PI, RADIANS, DEGREES, SIN, COS, TAN, ASIN, ACOS, ATAN."""
        if name == "PI":
            return math.pi
        if not values:
            return VALUE_ERROR
        value = _to_number(values[0])
        if name == "RADIANS":
            return math.radians(value)
        if name == "DEGREES":
            return math.degrees(value)
        if name == "SIN":
            return math.sin(value)
        if name == "COS":
            return math.cos(value)
        if name == "TAN":
            return math.tan(value)
        if name == "ASIN":
            if value < -1 or value > 1:
                return NUM_ERROR
            return math.asin(value)
        if name == "ACOS":
            if value < -1 or value > 1:
                return NUM_ERROR
            return math.acos(value)
        if name == "ATAN":
            return math.atan(value)
        return VALUE_ERROR

    # ── Advanced criteria functions ─────────────────────────────────────────

    def _advanced_criteria(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """AVERAGEIF, AVERAGEIFS, SUMIFS, COUNTIFS with multi-criteria support."""
        if name == "AVERAGEIF":
            return self._criteria("AVERAGEIF", args, sheet, seen, depth)
        if name == "SUMIFS":
            if len(args) < 3 or len(args) % 2 != 1:
                return VALUE_ERROR
            sum_range_node = args[0]
            sum_matrix = self._range_matrix(sum_range_node, sheet, seen, depth)
            if not sum_matrix:
                return 0.0
            num_criteria = (len(args) - 1) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_matrix = self._range_matrix(args[1 + i * 2], sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                crit_node = args[2 + i * 2]
                if crit_node[0] == "str":
                    criteria_list.append(crit_node[1])
                elif crit_node[0] == "num":
                    criteria_list.append(crit_node[1])
                else:
                    criteria_list.append(self._eval(crit_node, sheet, seen, depth))
            total = 0.0
            for r in range(len(sum_matrix)):
                for c in range(len(sum_matrix[r])):
                    all_match = True
                    for ci in range(num_criteria):
                        cr_mat = criteria_matrices[ci]
                        if r < len(cr_mat) and c < len(cr_mat[r]):
                            if not self._criteria_test(criteria_list[ci], cr_mat[r][c]):
                                all_match = False
                                break
                        else:
                            all_match = False
                            break
                    if all_match:
                        total += _to_number_or_zero(sum_matrix[r][c])
            return float(total)
        if name == "COUNTIFS":
            if len(args) < 2 or len(args) % 2 != 0:
                return VALUE_ERROR
            num_criteria = len(args) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_matrix = self._range_matrix(args[i * 2], sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                crit_node = args[i * 2 + 1]
                if crit_node[0] == "str":
                    criteria_list.append(crit_node[1])
                elif crit_node[0] == "num":
                    criteria_list.append(crit_node[1])
                else:
                    criteria_list.append(self._eval(crit_node, sheet, seen, depth))
            if not criteria_matrices:
                return 0.0
            max_rows = max(len(m) for m in criteria_matrices)
            max_cols = max((len(m[0]) if m else 0) for m in criteria_matrices)
            count = 0
            for r in range(max_rows):
                for c in range(max_cols):
                    all_match = True
                    for ci in range(num_criteria):
                        cr_mat = criteria_matrices[ci]
                        if r < len(cr_mat) and c < len(cr_mat[r]):
                            if not self._criteria_test(criteria_list[ci], cr_mat[r][c]):
                                all_match = False
                                break
                        else:
                            all_match = False
                            break
                    if all_match:
                        count += 1
            return float(count)
        if name == "AVERAGEIFS":
            if len(args) < 3 or len(args) % 2 != 1:
                return VALUE_ERROR
            average_range_node = args[0]
            average_matrix = self._range_matrix(average_range_node, sheet, seen, depth)
            if not average_matrix:
                return DIV_ZERO
            num_criteria = (len(args) - 1) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_matrix = self._range_matrix(args[1 + i * 2], sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                crit_node = args[2 + i * 2]
                if crit_node[0] == "str":
                    criteria_list.append(crit_node[1])
                elif crit_node[0] == "num":
                    criteria_list.append(crit_node[1])
                else:
                    criteria_list.append(self._eval(crit_node, sheet, seen, depth))
            total = 0.0
            count = 0
            for r in range(len(average_matrix)):
                for c in range(len(average_matrix[r])):
                    all_match = True
                    for ci in range(num_criteria):
                        cr_mat = criteria_matrices[ci]
                        if r < len(cr_mat) and c < len(cr_mat[r]):
                            if not self._criteria_test(criteria_list[ci], cr_mat[r][c]):
                                all_match = False
                                break
                        else:
                            all_match = False
                            break
                    if all_match:
                        total += _to_number_or_zero(average_matrix[r][c])
                        count += 1
            if count == 0:
                return DIV_ZERO
            return float(total) / count
        return VALUE_ERROR


def compute_value(
    value_type: str,
    value: Any,
    formula: Optional[str],
    resolver: CellResolver,
    sheet: Optional[str] = None,
    named_ranges: Optional[Dict[str, Any]] = None,
    _evaluator_cache: Optional[Dict[int, FormulaEvaluator]] = None,
) -> Any:
    """Top-level entry: return the display/computed value for a cell.

    ``sheet`` defaults to ``None`` (single-sheet context); callers that
    resolve against a real workbook should pass the sheet id so cross-sheet
    and same-sheet references hit the right rows.

    An optional *named_ranges* dict maps range names (case-insensitive) to
    cell references (e.g., ``{"SALES": "A1:A100"}``).

    An optional *_evaluator_cache* dict (keyed by ``id(resolver)``) allows
    callers that evaluate many cells with the same resolver to reuse a
    single ``FormulaEvaluator`` instance, preserving its memoization cache
    across calls.  Without this, every ``compute_value`` call created a
    fresh evaluator, discarding previously computed results.
    """
    if value_type == "formula" and formula:
        if _evaluator_cache is not None:
            key = id(resolver)
            evaluator = _evaluator_cache.get(key)
            if evaluator is None:
                evaluator = FormulaEvaluator(resolver, named_ranges=named_ranges)
                _evaluator_cache[key] = evaluator
            return evaluator.evaluate_cell(sheet, -1, -1, formula)
        return FormulaEvaluator(resolver, named_ranges=named_ranges).evaluate_cell(
            sheet, -1, -1, formula
        )
    return value
