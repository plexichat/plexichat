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
    _criteria_operator,
    _numeric,
    _to_number,
    _to_number_or_zero,
    _to_text,
    is_error,
    parse_cell_ref,
    parse_formula,
)

# Functions whose arguments are flattened into a ``values`` list by ``_call``.
# Kept beside the dispatch below so the two stay in step: an unknown name must
# still report #NAME? rather than surface an error that happened to sit in its
# arguments.
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
        "EOMONTH",
        "EDATE",
        "WEEKDAY",
        "WEEKNUM",
        "NETWORKDAYS",
        "WORKDAY",
        "PI",
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
    }
)

# Functions that classify an error operand instead of propagating it. COUNT
# counts numbers only, COUNTA counts anything non-empty (an error is data), and
# COUNTBLANK counts an error cell as non-blank. The *IFS family matches an
# error cell as "no match" rather than failing the whole formula.
_ERROR_TOLERANT_FUNCTIONS = frozenset(
    {"COUNT", "COUNTA", "COUNTBLANK", "SUMIFS", "COUNTIFS", "AVERAGEIFS"}
)


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
        if kind == "blank":
            # An omitted argument is Excel's "missing" operand, which behaves
            # exactly like a blank cell (0 in arithmetic, "" as text).
            return None
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
            # #DIV/0!.
            condition = self._eval(args[0], sheet, seen, depth)
            if is_error(condition):
                return condition
            if self._truthy(condition):
                return self._eval(args[1], sheet, seen, depth)
            if len(args) == 3:
                return self._eval(args[2], sheet, seen, depth)
            return False
        if name in ("AND", "OR"):
            if not args:
                return VALUE_ERROR
            evaluated = [self._eval(arg, sheet, seen, depth) for arg in args]
            # An error operand propagates, as it does from every other
            # value-based function: AND(1/0) is #DIV/0!, not TRUE.
            for value in evaluated:
                if is_error(value):
                    return value
            results = [self._truthy(value) for value in evaluated]
            return all(results) if name == "AND" else any(results)
        if name == "NOT":
            if len(args) != 1:
                return VALUE_ERROR
            value = self._eval(args[0], sheet, seen, depth)
            if is_error(value):
                return value
            return not self._truthy(value)
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
        # Which entries were read out of a reference, running parallel to
        # ``values``. Excel coerces a text *argument* (SUM("2") is 2) but never
        # a value that came from a reference, so the aggregates need the
        # provenance and not just the flattened values.
        from_reference: List[bool] = []
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
        if name in _VALUE_FUNCTIONS and name not in _ERROR_TOLERANT_FUNCTIONS:
            for value in values:
                if is_error(value):
                    return value
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
        if name in (
            "ROUND",
            "ROUNDUP",
            "ROUNDDOWN",
            "MOD",
            "POWER",
            "CEILING",
            "FLOOR",
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
            # them, exactly the way a range argument does.
            return list(value), True
        return [value], False

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
        # wrong total (``SUM(1, 1/0)`` was 1, not ``#DIV/0!``).
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
        if not values:
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
            return float(len(_to_text(value)))
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
            # Excel's MOD is n - d*INT(n/d), so the result carries the sign of
            # the divisor. math.fmod truncates toward zero instead and returned
            # the dividend's sign, so MOD(-7, 2) was -1 where Excel says 1.
            number = _to_number(a)
            return number - divisor * math.floor(number / divisor)
        if name == "POWER":
            try:
                base = _to_number(a)
                exponent = _to_number(b)
            except ValueError as exc:
                return str(exc)
            # A negative base with a fractional exponent and any overflow are
            # #NUM!; zero to a negative power is 1 / 0**n, i.e. #DIV/0!.
            if base == 0 and exponent < 0:
                return DIV_ZERO
            try:
                return math.pow(base, exponent)
            except (ValueError, OverflowError):
                return NUM_ERROR
        if name in ("ROUND", "ROUNDUP", "ROUNDDOWN"):
            try:
                digits = int(_to_number(b))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            # Spreadsheets cap rounding precision at 15 digits. Without a
            # clamp, ``ROUND(1, 99999999999)`` computes ``10 ** digits`` as a
            # multi-gigabyte big integer and stalls/OOMs the viewport read.
            digits = max(-15, min(digits, 15))
            factor = 10**digits
            scaled = _to_number(a) * factor
            magnitude = abs(scaled)
            # Excel breaks ties away from zero, so ``round()`` (which breaks
            # them to even) and a raw ``ceil``/``floor`` were both wrong for
            # negative numbers: ROUND(-2.5) gave -2 and ROUNDUP(-2.1) gave -2.
            # A relative epsilon absorbs binary-float noise (2.675 * 100 lands
            # just below 267.5) without moving a genuinely sub-half value; it
            # is capped so it can never shift a large magnitude by a step.
            epsilon = min(magnitude * 1e-12, 1e-9)
            if name == "ROUND":
                rounded = math.floor(magnitude + 0.5 + epsilon)
            elif name == "ROUNDUP":
                rounded = math.ceil(magnitude - epsilon)
            else:
                rounded = math.floor(magnitude + epsilon)
            return math.copysign(rounded, scaled) / factor
        if name in ("CEILING", "FLOOR"):
            try:
                number = _to_number(a)
                significance = _to_number(b)
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            if significance == 0:
                return DIV_ZERO
            # Excel rejects a positive number with a negative significance
            # instead of silently returning a multiple of it.
            if number > 0 and significance < 0:
                return NUM_ERROR
            if number == 0:
                return 0.0
            # When the signs match the magnitude rounds away from zero. When
            # the number is negative and the significance positive it is the
            # mirror image: CEILING moves toward zero and FLOOR away from it.
            same_sign = (number < 0) == (significance < 0)
            away_from_zero = same_sign == (name == "CEILING")
            magnitude = abs(number) / abs(significance)
            epsilon = min(magnitude * 1e-12, 1e-9)
            if away_from_zero:
                multiple = math.ceil(magnitude - epsilon)
            else:
                multiple = math.floor(magnitude + epsilon)
            result = multiple * abs(significance)
            if number < 0:
                result = -result
            # Snap to the 15 significant digits a spreadsheet keeps so the
            # multiply cannot leak binary noise (FLOOR(1.58, 0.1) was
            # 1.5000000000000002).
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
                start = int(_to_number(values[1]))
                num_chars = int(_to_number(values[2]))
            else:
                start = 1
                num_chars = int(_to_number(values[1])) if len(values) == 2 else 1
        except (ValueError, OverflowError) as exc:
            return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
        if start < 1 or num_chars < 0:
            return VALUE_ERROR
        if name == "LEFT":
            return text[:num_chars]
        if name == "RIGHT":
            # Python's text[-0:] is the whole string, so zero needs its own
            # branch to match the empty result Excel returns.
            return text[-num_chars:] if num_chars else ""
        offset = start - 1
        return text[offset : offset + num_chars]

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

    def _criteria_range(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> List[List[Any]]:
        """Resolve a criteria/sum range, accepting a single cell as 1×1.

        ``_range_matrix`` only describes ``A1:B2`` refs, so ``COUNTIF(B6, x)``
        and ``SUMIFS(..., B6, x)`` used to see an empty range and answer 0
        (or #DIV/0!) instead of testing B6.
        """
        matrix = self._range_matrix(node, sheet, seen, depth)
        if matrix or node[0] != "ref":
            return matrix
        ref_sheet, row, column = parse_cell_ref(node[1], self._named_ranges)
        self._used += 1
        if self._used > self._dependencies:
            raise ValueError(NUM_ERROR)
        target = ref_sheet or sheet
        return [[self._resolve(target, row, column, seen, depth + 1)]]

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
        matrix = self._criteria_range(args[0], sheet, seen, depth)
        if not matrix:
            return DIV_ZERO if name == "AVERAGEIF" else 0.0
        first = args[1]
        if first[0] == "str":
            criteria: Any = first[1]
        elif first[0] == "num":
            criteria = first[1]
        else:
            criteria = self._eval(first, sheet, seen, depth)
        if is_error(criteria):
            # An error *criteria* argument propagates; an error cell inside the
            # criteria range only fails to match.
            return criteria
        sum_matrix = matrix
        if len(args) == 3:
            sum_matrix = self._criteria_range(args[2], sheet, seen, depth)
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
            return _to_text(value).casefold() == criteria.casefold()
        if isinstance(value, str) or isinstance(value, bool):
            return value == criteria
        try:
            return _to_number(value) == _to_number(criteria)
        except ValueError:
            return False
