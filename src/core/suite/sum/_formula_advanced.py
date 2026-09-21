"""Formula evaluator mixin: advanced text/date/trig/criteria functions."""

import datetime
import math
from typing import Any, List, Optional

from ._formula_core import FormulaEvaluatorCore
from ._formula_shared import (
    DIV_ZERO,
    ERROR_CODES,
    MAX_DATE_SERIAL,
    MAX_NETWORKDAYS_SPAN_DAYS,
    MAX_REPT_CHARS,
    MAX_WORKDAY_OFFSET_DAYS,
    N_A_ERROR,
    NUMERIC_TEXT_RE,
    NUM_ERROR,
    REF_ERROR,
    VALUE_ERROR,
    _DATE_EPOCH,
    _numeric,
    _to_number,
    _to_number_or_zero,
    _to_text,
    is_error,
    parse_cell_ref,
)


def _parse_numeric_text(raw: str, group_separator: str = ",") -> Optional[float]:
    """Read a numeric string the way Excel's VALUE does.

    ``float`` rejects trailing garbage but still accepts underscores
    (``float("1_0") == 10.0``), and neither path understood thousands
    separators, a leading currency symbol, a trailing percent sign or a
    parenthesised negative, so ``VALUE("1,234")`` returned #VALUE!.

    Returns ``None`` when the text is not a number, which the callers turn
    into ``#VALUE!``.
    """
    text = raw.strip()
    if not text:
        return None
    sign = 1.0
    if text.startswith("(") and text.endswith(")"):
        sign = -1.0
        text = text[1:-1].strip()
    scale = 1.0
    if text.endswith("%"):
        scale = 100.0
        text = text[:-1].strip()
    if text.startswith("$"):
        text = text[1:]
    if group_separator:
        text = text.replace(group_separator, "")
    if not NUMERIC_TEXT_RE.match(text):
        return None
    return sign * float(text) / scale


def _holiday_serials(values: List[Any]) -> set:
    """Collect the day-serials of an optional ``holidays`` argument.

    ``_expand_arg`` flattens a range into ``values``, so blanks and non-date
    cells (a stray label beside the real dates) can sit alongside them. Excel
    ignores those rather than failing the whole formula, so a value that does
    not coerce to a number is skipped instead of poisoning the result.
    """
    serials = set()
    for value in values:
        if value is None or is_error(value):
            continue
        try:
            serials.add(int(_to_number(value)))
        except (TypeError, ValueError):
            continue
    return serials


# First day of the week (0 = Sunday) for Excel's WEEKNUM return types. Type 21
# is ISO-8601 and uses its own algorithm, so it is absent here on purpose.
_WEEK_START_DAYS = {
    1: 0,  # week begins Sunday (the default)
    2: 1,  # week begins Monday
    11: 1,
    12: 2,
    13: 3,
    14: 4,
    15: 5,
    16: 6,
    17: 0,
}


class FormulaEvaluatorAdvanced(FormulaEvaluatorCore):
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
            # A single-cell block collapses to its value, as Excel returns the
            # referenced value itself; anything larger stays a flat value list.
            if len(result) == 1:
                return result[0]
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
                # A single cell (COUNTBLANK(A1)) is not a ":" range, so
                # _range_matrix cannot describe it; Excel still reports
                # whether that one cell is blank. An error cell is not blank.
                if args[0][0] == "ref":
                    value = self._eval(args[0], sheet, seen, depth)
                    return 1.0 if value is None or value == "" else 0.0
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
                decimal_sep = _to_text(values[1])
            if len(values) == 3:
                group_sep = _to_text(values[2])
            if not decimal_sep:
                return VALUE_ERROR
            # Remove group separators and replace the decimal separator, then
            # validate the whole string rather than parsing a prefix.
            text = _to_text(values[0]).replace(group_sep, "").replace(decimal_sep, ".")
            number = _parse_numeric_text(text, "")
            return VALUE_ERROR if number is None else number
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
                # Excel reports a failed FIND/SEARCH as #VALUE!, not #N/A.
                return VALUE_ERROR
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
                # Excel reports a failed FIND/SEARCH as #VALUE!, not #N/A.
                return VALUE_ERROR
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
            # Bound the allocation: REPT("x", 10**9) would otherwise try to
            # materialize a multi-gigabyte string on a read path.
            if len(text) * times > MAX_REPT_CHARS:
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
            if return_type == 3:  # 0=Monday, 6=Sunday
                return float(weekday - 1)
            # Types 1 and 17 are Sunday-first, 2 and 11 Monday-first, 12
            # Tuesday-first, ... 16 Saturday-first. _WEEK_START_DAYS holds the
            # Sunday-based index of each type's first day (WEEKNUM uses the
            # same table). An unknown type was previously accepted and answered
            # the raw isoweekday() instead of #NUM!.
            start_day = _WEEK_START_DAYS.get(return_type)
            if start_day is None:
                return NUM_ERROR
            dow0 = weekday % 7  # 0=Sunday .. 6=Saturday
            return float((dow0 - start_day) % 7 + 1)
        elif name == "WEEKNUM":
            # WEEKNUM(serial_number, [return_type])
            if len(values) < 1 or len(values) > 2:
                return VALUE_ERROR
            try:
                serial = int(_to_number(values[0]))
                return_type = int(_to_number(values[1])) if len(values) == 2 else 1
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            if abs(serial) > MAX_DATE_SERIAL:
                return NUM_ERROR
            try:
                date_val = _DATE_EPOCH + datetime.timedelta(days=serial)
            except (ValueError, OverflowError):
                return NUM_ERROR
            # Type 21 is ISO-8601: week 1 holds the year's first Thursday.
            if return_type == 21:
                return float(date_val.isocalendar()[1])
            start_day = _WEEK_START_DAYS.get(return_type)
            if start_day is None:
                return NUM_ERROR
            # Every other type numbers from the week containing January 1 of
            # the date's own year, differing only in the first day of the
            # week. ``isoweekday() % 7`` re-indexes Monday..Sunday onto
            # Sunday-first 0..6 to match ``start_day``.
            week_start = date_val - datetime.timedelta(
                days=(date_val.isoweekday() % 7 - start_day) % 7
            )
            jan1 = datetime.date(date_val.year, 1, 1)
            first_week_start = jan1 - datetime.timedelta(
                days=(jan1.isoweekday() % 7 - start_day) % 7
            )
            return float((week_start - first_week_start).days // 7 + 1)
        elif name == "NETWORKDAYS":
            # NETWORKDAYS(start_date, end_date, [holidays])
            # A holiday range is flattened into ``values`` after the two
            # required arguments, so only the lower bound is fixed here.
            if len(values) < 2:
                return VALUE_ERROR
            try:
                start_serial = int(_to_number(values[0]))
                end_serial = int(_to_number(values[1]))
            except ValueError:
                return VALUE_ERROR
            if max(abs(start_serial), abs(end_serial)) > MAX_DATE_SERIAL:
                return NUM_ERROR
            holidays = _holiday_serials(values[2:])
            start_date = _DATE_EPOCH + datetime.timedelta(days=start_serial)
            end_date = _DATE_EPOCH + datetime.timedelta(days=end_serial)
            # Bound the day-by-day loop: NETWORKDAYS(1, 10**9) would otherwise
            # stall the evaluating thread for hours on a read path.
            if abs((end_date - start_date).days) > MAX_NETWORKDAYS_SPAN_DAYS:
                return NUM_ERROR
            count = 0
            current = start_date
            while current <= end_date:
                # Monday-Friday, excluding listed holidays. A holiday that
                # lands on a weekend is already excluded, so it cannot
                # subtract twice.
                if (
                    current.isoweekday() <= 5
                    and (current - _DATE_EPOCH).days not in holidays
                ):
                    count += 1
                current += datetime.timedelta(days=1)
            return float(count)
        elif name == "WORKDAY":
            # WORKDAY(start_date, days, [holidays])
            if len(values) < 2:
                return VALUE_ERROR
            try:
                start_serial = int(_to_number(values[0]))
                work_days = int(_to_number(values[1]))
            except ValueError:
                return VALUE_ERROR
            if abs(start_serial) > MAX_DATE_SERIAL:
                return NUM_ERROR
            holidays = _holiday_serials(values[2:])
            current = _DATE_EPOCH + datetime.timedelta(days=start_serial)
            # A negative day count walks backwards, as every other
            # spreadsheet does; zero days is the start date itself.
            remaining = abs(work_days)
            step = 1 if work_days >= 0 else -1
            # Bound the walk: WORKDAY(1, 10**9) would otherwise loop
            # unbounded on a read path.
            if remaining > MAX_WORKDAY_OFFSET_DAYS:
                return NUM_ERROR
            while remaining > 0:
                current += datetime.timedelta(days=step)
                # Monday-Friday, excluding listed holidays.
                if (
                    current.isoweekday() <= 5
                    and (current - _DATE_EPOCH).days not in holidays
                ):
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
            sum_matrix = self._criteria_range(sum_range_node, sheet, seen, depth)
            if not sum_matrix:
                return 0.0
            num_criteria = (len(args) - 1) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_matrix = self._criteria_range(args[1 + i * 2], sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                crit_node = args[2 + i * 2]
                if crit_node[0] == "str":
                    criteria_list.append(crit_node[1])
                elif crit_node[0] == "num":
                    criteria_list.append(crit_node[1])
                else:
                    criteria = self._eval(crit_node, sheet, seen, depth)
                    if is_error(criteria):
                        # An error criteria operand propagates; an error cell
                        # inside a criteria range only fails to match.
                        return criteria
                    criteria_list.append(criteria)
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
                cr_matrix = self._criteria_range(args[i * 2], sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                crit_node = args[i * 2 + 1]
                if crit_node[0] == "str":
                    criteria_list.append(crit_node[1])
                elif crit_node[0] == "num":
                    criteria_list.append(crit_node[1])
                else:
                    criteria = self._eval(crit_node, sheet, seen, depth)
                    if is_error(criteria):
                        # An error criteria operand propagates; an error cell
                        # inside a criteria range only fails to match.
                        return criteria
                    criteria_list.append(criteria)
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
            average_matrix = self._criteria_range(
                average_range_node, sheet, seen, depth
            )
            if not average_matrix:
                return DIV_ZERO
            num_criteria = (len(args) - 1) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_matrix = self._criteria_range(args[1 + i * 2], sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                crit_node = args[2 + i * 2]
                if crit_node[0] == "str":
                    criteria_list.append(crit_node[1])
                elif crit_node[0] == "num":
                    criteria_list.append(crit_node[1])
                else:
                    criteria = self._eval(crit_node, sheet, seen, depth)
                    if is_error(criteria):
                        # An error criteria operand propagates; an error cell
                        # inside a criteria range only fails to match.
                        return criteria
                    criteria_list.append(criteria)
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
                    if all_match and _numeric(average_matrix[r][c]):
                        # As in AVERAGEIF: only the numeric entries of the
                        # average range are averaged; text and blanks are
                        # ignored rather than counted as zero.
                        total += _to_number_or_zero(average_matrix[r][c])
                        count += 1
            if count == 0:
                return DIV_ZERO
            return float(total) / count
        return VALUE_ERROR
