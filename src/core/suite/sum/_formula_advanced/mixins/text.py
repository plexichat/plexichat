"""Formula evaluator mixin: text, aggregate and branching functions."""

import datetime
import math
from typing import Any, List, Optional
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    ERROR_CODES,
    MAX_REPT_CHARS,
    NUM_ERROR,
    N_A_ERROR,
    VALUE_ERROR,
    _DATE_EPOCH,
    _numeric,
    _slot_text,
    _to_number,
    _to_text,
    _utf16_find,
    _utf16_first_unit,
    _utf16_len,
    _utf16_slice,
    is_error,
)
from ..helpers.numbers import _parse_numeric_text, _read_number, _same_value


class TextMixin(AdvancedEvaluatorBase):
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
        text = _slot_text(values[0])
        old = _slot_text(values[1])
        new = _slot_text(values[2])
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

    def _advanced_aggregate(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """COUNTBLANK, SUMPRODUCT."""
        if name == "COUNTBLANK":
            if len(args) != 1:
                return VALUE_ERROR
            # A single cell (COUNTBLANK(A1)) is a 1x1 range, which
            # _range_matrix now describes. An error cell is not blank.
            matrix = self._range_matrix(args[0], sheet, seen, depth)
            if not matrix:
                return 0.0
            count = 0
            for row in matrix:
                for val in row:
                    # A zero cell is not blank: Excel's COUNTBLANK counts only
                    # genuinely empty cells and cells holding "".
                    if val is None or val == "":
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
                    # A computed argument is an array too: a scalar is a 1x1
                    # block and a nested OFFSET/INDEX result is one row, the
                    # shape INDEX returns for it. Answering #VALUE! made
                    # SUMPRODUCT(2, 3) an error rather than 6.
                    value = self._eval(arg, sheet, seen, depth)
                    if is_error(value):
                        return value
                    matrix = [list(value)] if isinstance(value, list) else [[value]]
                matrices.append(matrix)
            # All matrices must have same shape
            rows = len(matrices[0])
            cols = len(matrices[0][0]) if rows > 0 else 0
            for m in matrices:
                if len(m) != rows or (rows > 0 and len(m[0]) != cols):
                    return VALUE_ERROR
            # The shapes agree, so an error cell is read and propagates: the
            # client engine reports the mismatch first and the error second.
            for m in matrices:
                for row in m:
                    for val in row:
                        if is_error(val):
                            return val
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

    def _advanced_text(self, name: str, values: List[Any]) -> Any:
        """TEXT, VALUE, NUMBERVALUE, FIND, SEARCH, REPLACE, REPT, CHAR, CODE."""
        if name == "TEXT":
            # TEXT(value, format_text)
            if len(values) != 2:
                return VALUE_ERROR
            value = values[0]
            fmt = _slot_text(values[1])
            if _numeric(value):
                # Simple numeric formatting
                if fmt == "0":
                    # Excel rounds half away from zero to the requested
                    # precision -- the same rule ROUND follows. int() truncated
                    # instead, so TEXT(1234.5, "0") was "1234" and TEXT(2.5,
                    # "0") was "2".
                    magnitude = int(math.floor(abs(value) + 0.5))
                    return str(magnitude if value >= 0 else -magnitude)
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
            number = _parse_numeric_text(_to_text(values[0]))
            return VALUE_ERROR if number is None else number
        elif name == "NUMBERVALUE":
            # NUMBERVALUE(text, [decimal_separator], [group_separator])
            if len(values) < 1 or len(values) > 3:
                return VALUE_ERROR
            decimal_sep = "."
            group_sep = ","
            if len(values) >= 2:
                decimal_sep = _slot_text(values[1])
            if len(values) == 3:
                group_sep = _slot_text(values[2])
            if not decimal_sep:
                return VALUE_ERROR
            # Remove group separators and replace the decimal separator, then
            # validate the whole string rather than parsing a prefix.
            text = (
                _slot_text(values[0]).replace(group_sep, "").replace(decimal_sep, ".")
            )
            number = _parse_numeric_text(text, "")
            return VALUE_ERROR if number is None else number
        elif name == "FIND":
            # FIND(find_text, within_text, [start_num])
            if len(values) < 2 or len(values) > 3:
                return VALUE_ERROR
            find_text = _slot_text(values[0])
            within_text = _slot_text(values[1])
            start_num = 1
            if len(values) == 3:
                start_num = int(_to_number(values[2]))
            if start_num < 1:
                return VALUE_ERROR
            # The start and the answer are UTF-16 code units, as in Excel and
            # the client engine: FIND("😃", "😀😃😄") is 3, not the 2 Python's
            # code-point indexing answered.
            pos = _utf16_find(find_text, within_text, start_num - 1)
            if pos == -1:
                # Excel reports a failed FIND/SEARCH as #VALUE!, not #N/A.
                return VALUE_ERROR
            return float(pos + 1)
        elif name == "SEARCH":
            # SEARCH(find_text, within_text, [start_num])
            # Case-insensitive version of FIND
            if len(values) < 2 or len(values) > 3:
                return VALUE_ERROR
            find_text = _slot_text(values[0]).lower()
            within_text = _slot_text(values[1]).lower()
            start_num = 1
            if len(values) == 3:
                start_num = int(_to_number(values[2]))
            if start_num < 1:
                return VALUE_ERROR
            # Unit-based like FIND above: SEARCH("😃", "😀😃😄") is 3.
            pos = _utf16_find(find_text, within_text, start_num - 1)
            if pos == -1:
                # Excel reports a failed FIND/SEARCH as #VALUE!, not #N/A.
                return VALUE_ERROR
            return float(pos + 1)
        elif name == "REPLACE":
            # REPLACE(old_text, start_num, num_chars, new_text)
            if len(values) != 4:
                return VALUE_ERROR
            old_text = _slot_text(values[0])
            start_number = _to_number(values[1])
            count_number = _to_number(values[2])
            new_text = _slot_text(values[3])
            if start_number < 1:
                return VALUE_ERROR
            # Excel rejects a negative num_chars with #VALUE!, the same rule
            # LEFT/RIGHT/MID already apply above. Without the check the cut
            # ``start_num - 1 + num_chars`` fell below the insertion point and
            # the untouched prefix was duplicated: REPLACE("hello", 2, -1,
            # "X") answered "hXhello".
            if count_number < 0:
                return VALUE_ERROR
            # Unit-based like the slices: cutting inside an astral character
            # keeps the surviving half, as Excel and the client engine do.
            #
            # The cut is ``start_num - 1 + num_chars`` truncated as one sum,
            # not each part truncated first, so the two engines agree on a
            # fractional count.
            start = int(start_number) - 1
            cut = int(start_number - 1 + count_number)
            return (
                _utf16_slice(old_text, 0, start)
                + new_text
                + _utf16_slice(old_text, cut, None)
            )
        elif name == "REPT":
            # REPT(text, number_times)
            if len(values) != 2:
                return VALUE_ERROR
            text = _slot_text(values[0])
            count = _to_number(values[1])
            # The raw count, not its truncation: ``REPT("x", -0.5)`` is #VALUE!
            # in Excel and the client engine, where ``int()`` rounded it to 0.
            if count < 0:
                return VALUE_ERROR
            times = int(count)
            # Bound the allocation: REPT("x", 10**9) would otherwise try to
            # materialise a multi-gigabyte string on a read path. The bound
            # counts UTF-16 code units, the same units LEN reports, so the
            # client engine enforces the identical limit on its side.
            if _utf16_len(text) * times > MAX_REPT_CHARS:
                return VALUE_ERROR
            return text * times
        elif name == "CHAR":
            # CHAR(number): the character with that code (1-255 in Excel).
            if len(values) != 1:
                return VALUE_ERROR
            try:
                number = _to_number(values[0])
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            code = math.trunc(number)
            if code < 1 or code > 255:
                return VALUE_ERROR
            return chr(code)
        elif name == "CODE":
            # CODE(text): the code of the first character. That is the first
            # UTF-16 unit -- CODE("😀") is 55357, the high surrogate -- the
            # way the client engine's charCodeAt(0) reads it. Python's ord
            # reads the whole code point (128512) instead.
            if len(values) != 1:
                return VALUE_ERROR
            text = _to_text(values[0])
            if not text:
                return VALUE_ERROR
            return float(_utf16_first_unit(text))
        return VALUE_ERROR

    def _advanced_trig(self, name: str, values: List[Any]) -> Any:
        """PI, RADIANS, DEGREES, SIN, COS, TAN, ASIN, ACOS, ATAN."""
        if name == "PI":
            # PI() ignores its arguments on both engines, so the arity is
            # deliberately unchecked here.
            return math.pi
        # Every other function here takes exactly one argument: SIN(1, 2)
        # is #VALUE!, matching the client engine.
        if len(values) != 1:
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

    def _branched(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """CHOOSE, IFS and SWITCH, which evaluate only the branch they take.

        Excel leaves the untaken branches unevaluated, so ``IFS(TRUE, 1, TRUE,
        1/0)`` is 1 rather than #DIV/0!; evaluating every argument up front
        would fail the whole call instead.
        """
        if name == "CHOOSE":
            if len(args) < 2:
                return VALUE_ERROR
            index = self._scalar_or(args[0], sheet, seen, depth)
            if is_error(index):
                return index
            error, number = _read_number(index)
            if error is not None:
                return error
            position = math.floor(number)
            if position < 1 or position > len(args) - 1:
                return VALUE_ERROR
            return self._scalar_or(args[position], sheet, seen, depth)
        if name == "IFS":
            if len(args) < 2 or len(args) % 2 != 0:
                return VALUE_ERROR
            # Each condition and the chosen result is one value, so a matrix --
            # a range or a nested call -- follows the pooled rule (scalarize).
            for index in range(0, len(args), 2):
                condition = self._scalar_or(args[index], sheet, seen, depth)
                if is_error(condition):
                    return condition
                if self._truthy(condition):
                    return self._scalar_or(args[index + 1], sheet, seen, depth)
            return N_A_ERROR
        # SWITCH(expression, search1, result1, ..., [default])
        if len(args) < 3:
            return VALUE_ERROR
        expression = self._scalar_or(args[0], sheet, seen, depth)
        # An error operand propagates, as it does everywhere else: an error
        # *expression* or the error *search* the scan reaches is the answer,
        # where reading the error as just another value fell through to the
        # default. Only the operands the scan actually evaluates propagate,
        # which is the short-circuit rule the untaken branches already follow.
        # Each operand the scan reads is one value, so a matrix follows the
        # pooled rule (scalarize).
        if is_error(expression):
            return expression
        pairs = args[1:]
        for index in range(0, len(pairs) - 1, 2):
            search = self._scalar_or(pairs[index], sheet, seen, depth)
            if is_error(search):
                return search
            if _same_value(expression, search):
                return self._scalar_or(pairs[index + 1], sheet, seen, depth)
        if len(pairs) % 2 == 1:
            return self._scalar_or(pairs[-1], sheet, seen, depth)
        return N_A_ERROR
