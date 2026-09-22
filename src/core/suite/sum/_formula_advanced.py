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
    _compare_values,
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
    number = sign * float(text) / scale
    # A decimal that overflows a double ("1e999") reads as no number at all,
    # so VALUE reports #VALUE! rather than returning infinity.
    return number if math.isfinite(number) else None


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


# The largest value Excel still treats as an exact integer. GCD and LCM
# reject anything above it, and LCM rejects a result above it, rather than
# returning a number double-precision arithmetic can no longer represent
# exactly. Mirrors the client engine's MAX_EXACT_INTEGER.
MAX_EXACT_INTEGER = 2**53

# Excel's upper bound for each TIME() component; anything above it is #NUM!.
MAX_TIME_COMPONENT = 32767

# Formats DATEVALUE reads. Excel is locale-dependent here and the client
# engine leans on the host JavaScript date parser, so this covers the
# unambiguous shapes (ISO first, then the common US and day-month-name
# spellings) rather than pretending to be a full locale calendar.
_DATEVALUE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%m/%d/%y",
    "%d-%b-%y",
)


def _parse_date_text(text: str) -> Optional[datetime.date]:
    for pattern in _DATEVALUE_FORMATS:
        try:
            return datetime.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _time_of_day(serial: float) -> int:
    """The seconds past midnight the serial's fractional part names."""
    fraction = serial - math.floor(serial)
    return int(round(fraction * 86400.0)) % 86400


def _read_number(value: Any) -> tuple:
    """Coerce one value, returning ``(error, number)``.

    A function that cannot skip a value (SUMSQ, the cross-product sums, a
    rank or a percentile) reports the coercion failure instead of treating the
    value as absent, which is what the client engine's ``toNumber`` does.
    """
    try:
        return None, _to_number(value)
    except ValueError as exc:
        return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR, None


def _number_values(values: List[Any]) -> List[float]:
    """The numeric entries of a flattened argument list.

    Excel ignores text and blanks inside a statistical range, so a value that
    does not read as a number is skipped rather than failing the whole call.
    """
    numbers: List[float] = []
    for value in values:
        if value is None or is_error(value):
            continue
        if isinstance(value, bool):
            numbers.append(1.0 if value else 0.0)
            continue
        try:
            numbers.append(_to_number(value))
        except ValueError:
            continue
    return numbers


def _number_or_zero(value: Any) -> float:
    """Coerce a value the way Excel's "A" functions (AVERAGEA/MAXA/MINA) do.

    A number is itself, a logical is 1/0, text counts as its value only when
    the whole string reads as a number, and anything else counts as zero. A
    value is never dropped: the A variants count a text entry as zero, which
    is what makes them the A variants.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return _to_number(value)
    except ValueError:
        return 0.0


def _literal_error(values: List[Any], reference: Any) -> Optional[str]:
    """The error a value-based function has to propagate.

    An error *written into* the formula propagates, while an error stored in a
    referenced cell is skipped the way text is -- that is how the client
    engine's statistical helpers already read a range, and the shared parity
    table pins the two engines to the same answer.

    ``reference`` is either the per-value provenance list ``_call`` builds (one
    flag per entry in *values*) or the single flag ``_expand_arg`` returns for
    one whole argument.
    """
    if isinstance(reference, bool):
        if reference:
            return None
        return next((value for value in values if is_error(value)), None)
    for value, ranged in zip(values, reference):
        if is_error(value) and not ranged:
            return value
    return None


def _same_value(left: Any, right: Any) -> bool:
    """Whether SWITCH's expression matches a case, comparing types too.

    A bare ``==`` would match SWITCH(TRUE, 1, "x") because Python regards
    ``True`` and ``1`` as equal, where the client engine does not.
    """
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    if isinstance(left, str) != isinstance(right, str):
        return False
    return left == right


# ── numeric plumbing for the financial, distribution and combinatorics sets ──
#
# The probability functions need the normal CDF, the log-gamma and the two
# regularized incomplete integrals, none of which the standard library
# provides. They are ported from the client engine's statistical helpers and
# kept numerically identical, so a shared parity case lands on the same double
# in both engines.


def _finite(value: Any) -> Any:
    """Report #NUM! where double precision leaves the representable numbers.

    Excel answers #NUM! for an overflow or a result off the real line; letting
    an infinity or a NaN travel on would put a non-number onto the sheet, which
    is never a value a formula should produce.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return NUM_ERROR
    return value


def _pow_js(base: float, exponent: float) -> float:
    """``math.pow`` with JavaScript's NaN/infinity domain behaviour.

    The client engine computes with ``Math.pow``, which yields NaN where
    Python raises ``ValueError`` and infinity where it raises
    ``OverflowError``. The numeric solvers below compare against those values,
    so they have to see the same ones to converge on the same root.
    """
    try:
        return math.pow(base, exponent)
    except ValueError:
        return float("nan")
    except OverflowError:
        return float("inf")


def _flatten_argument(value: Any) -> List[Any]:
    """A whole range argument as a flat list, whatever shape it arrived in."""
    if isinstance(value, (list, tuple)):
        out: List[Any] = []
        for item in value:
            out.extend(_flatten_argument(item))
        return out
    return [value]


def _gaussian(z: float) -> float:
    """The standard normal CDF (Hart's algorithm)."""
    a1, a2, a3 = 0.254829592, -0.284496736, 1.421413741
    a4, a5, p = -1.453152027, 1.061405429, 0.3275911
    sign = -1.0 if z < 0 else 1.0
    x = abs(z) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return 0.5 * (1.0 + sign * y)


def _inverse_normal(p: float) -> float:
    """The inverse standard normal CDF (Acklam's rational approximation)."""
    a = [
        -39.6968302866538,
        220.946098424521,
        -275.928510446969,
        138.357751867269,
        -30.6647980661472,
        2.50662827745924,
    ]
    b = [
        -54.4760987982241,
        161.585836858041,
        -155.698979859887,
        66.8013118877197,
        -13.2806815528857,
    ]
    c = [
        -0.00778489400243029,
        -0.322396458041136,
        -2.40075827716184,
        -2.54973253934373,
        4.37466414146497,
        2.93816398269878,
    ]
    d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        ) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
    )


def _gammaln(g: float) -> float:
    """Natural log of the gamma function (Lanczos approximation, g > 0)."""
    coefficients = [
        76.18009172947146,
        -86.50532032941677,
        24.01409824083091,
        -1.231739572450155,
        0.1208650973866179e-2,
        -0.5395239384953e-5,
    ]
    x = g
    tmp = x + 5.5
    tmp -= (x + 0.5) * math.log(tmp)
    series = 1.000000000190015
    for coefficient in coefficients:
        x += 1.0
        series += coefficient / x
    return -tmp + math.log(2.5066282746310005 * series / g)


def _gamma_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if x <= 0:
        return 0.0
    if a <= 0:
        return float("nan")
    gln = _gammaln(a)
    if x < a + 1:
        ap = a
        total = 1.0 / a
        delta = total
        for _ in range(1000):
            ap += 1.0
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-12:
                break
        return total * math.exp(-x + a * math.log(x) - gln)
    b = x + 1.0 - a
    c = 1e300
    d = 1.0 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-300:
            d = 1e-300
        c = b + an / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - gln) * h


def _betacf(x: float, a: float, b: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    max_iterations, epsilon, tiny = 200, 3e-12, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def _betai(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        a * math.log(x)
        + b * math.log(1 - x)
        - (_gammaln(a) + _gammaln(b) - _gammaln(a + b))
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(x, a, b) / a
    return 1.0 - front * _betacf(1 - x, b, a) / b


def _factorial(n: int) -> float:
    result = 1.0
    for i in range(2, n + 1):
        result *= i
        if not math.isfinite(result):
            # A factorial past 170 leaves the doubles. Returning as soon as it
            # overflows keeps a huge argument from spinning a loop that cannot
            # produce a representable number.
            return float("inf")
    return result


def _combin(n: int, k: int) -> float:
    """The binomial coefficient, rounded the way the client's Math.round does."""
    result = 1.0
    for i in range(k):
        result = result * (n - i) / (i + 1)
        if not math.isfinite(result):
            return float("inf")
    return float(math.floor(result + 0.5))


def _date_serial(value: Any) -> Optional[float]:
    """A date argument as an Excel serial, which is how XNPV reads it."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        parsed = _parse_date_text(value.strip())
        if parsed is None:
            return None
        return float((parsed - _DATE_EPOCH).days)
    return None


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
            # Excel rejects a year outside 0..9999, and adds 1900 to a year in
            # 0..1899 (DATE(108,1,2) is 2008-01-02). Without the second rule a
            # small year built a date in the year itself instead of in the
            # 1900s, which is what the client engine (and Excel) report.
            if year < 0 or year > 9999:
                return NUM_ERROR
            if year < 1900:
                year += 1900
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
            # A single cell is a 1x1 block, and a nested call (OFFSET) that
            # resolved to a block of values is a one-row block. Both used to
            # reach _range_matrix, which only describes "A1:B2" refs, so a
            # formula naming one cell came back #REF!.
            matrix = self._criteria_range(args[0], sheet, seen, depth)
            if not matrix:
                value = self._eval(args[0], sheet, seen, depth)
                if is_error(value):
                    return value
                matrix = [list(value)] if isinstance(value, list) else [[value]]
            # Excel truncates a fractional offset, rejects a negative one with
            # #VALUE!, and reads a 0 as "every row" / "every column" -- so
            # INDEX(A1:C3, 0, 2) is the whole second column, which SUM can add
            # up, rather than the #REF! this used to answer.
            row_num = int(_to_number(self._eval(args[1], sheet, seen, depth)))
            col_num = 1
            if len(args) == 3:
                col_num = int(_to_number(self._eval(args[2], sheet, seen, depth)))
            if row_num < 0 or col_num < 0:
                return VALUE_ERROR
            width = max((len(row) for row in matrix), default=0)
            if row_num > len(matrix) or col_num > width:
                return REF_ERROR
            rows = matrix if row_num == 0 else [matrix[row_num - 1]]
            block = [row if col_num == 0 else [row[col_num - 1]] for row in rows]
            # A block collapses to a flat value list, the shape OFFSET returns
            # here, and a 1x1 block to the value itself.
            cells = [cell for row in block for cell in row]
            return cells[0] if len(cells) == 1 else cells
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
                # Sorted ascending, so the answer is the last entry <= the
                # lookup value. Entries are ranked the way Excel orders types
                # rather than coerced with _to_number, which raised ValueError
                # on the first text entry and left the scan reporting the
                # length of the vector for every text lookup.
                for i, val in enumerate(flat):
                    if _compare_values(val, lookup_value) > 0:
                        return float(i) if i > 0 else N_A_ERROR
                return float(len(flat))
            elif match_type == -1:  # Greater than (sorted descending)
                for i, val in enumerate(flat):
                    if _compare_values(val, lookup_value) < 0:
                        return float(i) if i > 0 else N_A_ERROR
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
            # LOOKUP assumes sorted ascending, finds last value <= lookup_value.
            # The comparison ranks types the way Excel orders them, because
            # _to_number raised ValueError on the first text entry and the scan
            # then reported #N/A for a vector of text entirely.
            best_idx = -1
            for i, val in enumerate(flat):
                if _compare_values(val, lookup_value) <= 0:
                    best_idx = i
                else:
                    # Sorted ascending: once the vector passes the lookup value
                    # every later entry does too, so the scan can stop.
                    break
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
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            try:
                start_date = _DATE_EPOCH + datetime.timedelta(days=start_serial)
                # Add months
                month = start_date.month + months
                year = start_date.year + (month - 1) // 12
                month = (month - 1) % 12 + 1
                # Get last day of month
                if month == 12:
                    end_date = datetime.date(year + 1, 1, 1) - datetime.timedelta(
                        days=1
                    )
                else:
                    end_date = datetime.date(year, month + 1, 1) - datetime.timedelta(
                        days=1
                    )
            except (ValueError, OverflowError):
                # A serial (or month offset) that names no representable date
                # is #NUM!, the way Excel reports it -- and the way the rest of
                # this module already handles a missing date.
                return NUM_ERROR
            return float((end_date - _DATE_EPOCH).days)
        elif name == "EDATE":
            # EDATE(start_date, months)
            if len(values) != 2:
                return VALUE_ERROR
            try:
                start_serial = int(_to_number(values[0]))
                months = int(_to_number(values[1]))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            try:
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
            except (ValueError, OverflowError):
                # A serial (or month offset) that names no representable date
                # is #NUM!, not an OverflowError escaping the engine.
                return NUM_ERROR
            return float((end_date - _DATE_EPOCH).days)
        elif name == "WEEKDAY":
            # WEEKDAY(serial_number, [return_type])
            if len(values) < 1 or len(values) > 2:
                return VALUE_ERROR
            try:
                serial = int(_to_number(values[0]))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            return_type = 1
            if len(values) == 2:
                try:
                    return_type = int(_to_number(values[1]))
                except (ValueError, OverflowError) as exc:
                    return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            try:
                date_val = _DATE_EPOCH + datetime.timedelta(days=serial)
            except (ValueError, OverflowError):
                # A serial outside the representable date range is #NUM!.
                return NUM_ERROR
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
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
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
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
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

    # ── Functions adopted from the client engine ────────────────────────────
    #
    # The client engine implements 249 functions and this one a deliberately
    # compact subset. The block below is the README's "adopt next" list --
    # cheap functions over machinery that is already here, added so a sheet
    # that uses them reads the same on both engines.

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
            index = self._eval(args[0], sheet, seen, depth)
            if is_error(index):
                return index
            error, number = _read_number(index)
            if error is not None:
                return error
            position = math.floor(number)
            if position < 1 or position > len(args) - 1:
                return VALUE_ERROR
            return self._eval(args[position], sheet, seen, depth)
        if name == "IFS":
            if len(args) < 2 or len(args) % 2 != 0:
                return VALUE_ERROR
            for index in range(0, len(args), 2):
                condition = self._eval(args[index], sheet, seen, depth)
                if is_error(condition):
                    return condition
                if self._truthy(condition):
                    return self._eval(args[index + 1], sheet, seen, depth)
            return N_A_ERROR
        # SWITCH(expression, search1, result1, ..., [default])
        if len(args) < 3:
            return VALUE_ERROR
        expression = self._eval(args[0], sheet, seen, depth)
        pairs = args[1:]
        for index in range(0, len(pairs) - 1, 2):
            search = self._eval(pairs[index], sheet, seen, depth)
            if _same_value(expression, search):
                return self._eval(pairs[index + 1], sheet, seen, depth)
        if len(pairs) % 2 == 1:
            return self._eval(pairs[-1], sheet, seen, depth)
        return N_A_ERROR

    def _scalar_argument(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> tuple:
        """Evaluate one argument that must be a single value.

        Returns ``(error, value)``. A whole range where a scalar belongs is
        #VALUE!, which is what the client engine's ``toNumber`` reports for an
        array.
        """
        values, _ = self._expand_arg(node, sheet, seen, depth)
        if len(values) != 1:
            return VALUE_ERROR, None
        if is_error(values[0]):
            return values[0], None
        return None, values[0]

    def _percentile(self, numbers: List[float], percent: float) -> Any:
        """PERCENTILE.INC: linear interpolation between order statistics."""
        if not numbers or percent < 0 or percent > 1:
            return NUM_ERROR
        numbers.sort()
        rank = percent * (len(numbers) - 1)
        low = math.floor(rank)
        high = math.ceil(rank)
        if low == high:
            return float(numbers[low])
        return float(numbers[low] + (numbers[high] - numbers[low]) * (rank - low))

    def _structured(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """LARGE, SMALL, RANK, PERCENTILE, QUARTILE and the SUMX2* cross-sums.

        These need their arguments kept apart -- the second is a rank, a
        percentile or a parallel array rather than more data to pool -- so
        they read the raw argument nodes instead of one flattened list.
        """
        if name in ("LARGE", "SMALL"):
            if len(args) != 2:
                return VALUE_ERROR
            data, provided = self._expand_arg(args[0], sheet, seen, depth)
            error = _literal_error(data, provided)
            if error is not None:
                return error
            numbers = _number_values(data)
            error, rank_value = self._scalar_argument(args[1], sheet, seen, depth)
            if error is not None:
                return error
            error, rank_number = _read_number(rank_value)
            if error is not None:
                return error
            rank = math.floor(rank_number)
            if rank < 1 or rank > len(numbers):
                return NUM_ERROR
            numbers.sort()
            # The k-th largest is the (n-k)-th smallest.
            return float(numbers[-rank] if name == "LARGE" else numbers[rank - 1])
        if name == "RANK":
            if len(args) < 2 or len(args) > 3:
                return VALUE_ERROR
            error, target_value = self._scalar_argument(args[0], sheet, seen, depth)
            if error is not None:
                return error
            error, target = _read_number(target_value)
            if error is not None:
                return error
            ascending = 0.0
            if len(args) == 3:
                error, order_value = self._scalar_argument(args[2], sheet, seen, depth)
                if error is not None:
                    return error
                error, ascending = _read_number(order_value)
                if error is not None:
                    return error
            data, provided = self._expand_arg(args[1], sheet, seen, depth)
            error = _literal_error(data, provided)
            if error is not None:
                return error
            # Competition ranking with no sort and no intermediate array: the
            # rank is 1 plus the count of values strictly greater (descending)
            # or strictly less (ascending) than the target. Ties share the top
            # rank of their set, matching Excel.
            count = 0
            found = False
            for value in data:
                if value is None or is_error(value):
                    continue
                error, number = _read_number(value)
                if error is not None:
                    continue
                if number == target:
                    found = True
                elif (number < target) if ascending else (number > target):
                    count += 1
            if not found:
                return N_A_ERROR
            return float(count + 1)
        if name in ("PERCENTILE", "QUARTILE"):
            if len(args) != 2:
                return VALUE_ERROR
            data, provided = self._expand_arg(args[0], sheet, seen, depth)
            error = _literal_error(data, provided)
            if error is not None:
                return error
            numbers = _number_values(data)
            error, percent_value = self._scalar_argument(args[1], sheet, seen, depth)
            if error is not None:
                return error
            error, percent = _read_number(percent_value)
            if error is not None:
                return error
            if name == "QUARTILE":
                quartile = math.floor(percent)
                if quartile < 0 or quartile > 4:
                    return NUM_ERROR
                percent = quartile / 4
            return self._percentile(numbers, percent)
        if name in ("SUMX2MY2", "SUMX2PY2", "SUMXY"):
            if len(args) != 2:
                return VALUE_ERROR
            left, left_ref = self._expand_arg(args[0], sheet, seen, depth)
            right, right_ref = self._expand_arg(args[1], sheet, seen, depth)
            error = _literal_error(left, left_ref) or _literal_error(right, right_ref)
            if error is not None:
                return error
            if len(left) != len(right):
                return VALUE_ERROR
            total = 0.0
            for first, second in zip(left, right):
                error, x = _read_number(first)
                if error is not None:
                    return error
                error, y = _read_number(second)
                if error is not None:
                    return error
                if name == "SUMXY":
                    total += x * y
                elif name == "SUMX2MY2":
                    total += x * x - y * y
                else:
                    total += x * x + y * y
            return total
        return VALUE_ERROR

    def _adopted_statistical(
        self, name: str, values: List[Any], from_reference: List[bool]
    ) -> Any:
        """MEDIAN, MODE, STDEV, VAR, AVERAGEA, MAXA, MINA.

        These pool every argument into one set of numbers, so they read the
        flattened values but decide themselves how to treat an error operand:
        one written into the formula propagates while one stored in a cell is
        skipped, exactly as the client engine reads it.
        """
        error = _literal_error(values, from_reference)
        if error is not None:
            return error
        if name in ("AVERAGEA", "MAXA", "MINA"):
            # The A variants count a present value that is not a number (text)
            # as zero rather than dropping it, but a blank is still skipped.
            numbers = [
                _number_or_zero(value)
                for value in values
                if value is not None and not is_error(value)
            ]
            if name == "AVERAGEA":
                if not numbers:
                    return DIV_ZERO
                return float(sum(numbers)) / len(numbers)
            if not numbers:
                return 0
            return float(max(numbers) if name == "MAXA" else min(numbers))
        numbers = _number_values(values)
        if name == "MEDIAN":
            if not numbers:
                return NUM_ERROR
            numbers.sort()
            middle = len(numbers) // 2
            if len(numbers) % 2 == 1:
                return float(numbers[middle])
            return (numbers[middle - 1] + numbers[middle]) / 2
        if name == "MODE":
            if not numbers:
                return NUM_ERROR
            counts: dict = {}
            best: Any = None
            best_count = 0
            for number in numbers:
                count = counts.get(number, 0) + 1
                counts[number] = count
                if count > best_count:
                    best_count = count
                    best = number
            # A set where every value appears once has no mode at all.
            if best_count == 1:
                return NUM_ERROR
            return float(best)
        if name in ("STDEV", "VAR"):
            if len(numbers) < 2:
                return DIV_ZERO
            mean = sum(numbers) / len(numbers)
            total = sum((value - mean) ** 2 for value in numbers)
            variance = total / (len(numbers) - 1)
            return math.sqrt(variance) if name == "STDEV" else variance
        return VALUE_ERROR

    def _adopted_single(self, name: str, values: List[Any]) -> Any:
        """The adopted functions that take one scalar (or none).

        SIGN, GCD, LCM, SUMSQ, the hyperbolic and two-argument trig, the
        time-of-day readers, TODAY/NOW and the CLEAN/T/N/IS* additions.
        """
        if name in ("TODAY", "NOW"):
            if values:
                return VALUE_ERROR
            now = datetime.datetime.now()
            if name == "TODAY":
                return float((now.date() - _DATE_EPOCH).days)
            return (now - datetime.datetime(1899, 12, 30)).total_seconds() / 86400.0
        if name == "ERROR.TYPE":
            if len(values) != 1:
                return VALUE_ERROR
            return {
                DIV_ZERO: 2,
                VALUE_ERROR: 3,
                REF_ERROR: 4,
                "#NAME?": 5,
                NUM_ERROR: 6,
                N_A_ERROR: 7,
                "#CYCLE!": 8,
            }.get(values[0], N_A_ERROR)
        if not values:
            return VALUE_ERROR
        if name in ("ISLOGICAL", "ISNONTEXT"):
            if len(values) != 1:
                return VALUE_ERROR
            value = values[0]
            if name == "ISLOGICAL":
                return isinstance(value, bool)
            return not (isinstance(value, str) and not is_error(value))
        if name == "T":
            if len(values) != 1:
                return VALUE_ERROR
            return values[0] if isinstance(values[0], str) else ""
        if name == "CLEAN":
            if len(values) != 1:
                return VALUE_ERROR
            # Excel strips the non-printing control characters, which are the
            # C0 range and the C1 range.
            return "".join(
                character
                for character in _to_text(values[0])
                if not (0x00 <= ord(character) <= 0x1F)
                and not (0x7F <= ord(character) <= 0x9F)
            )
        if name in ("N", "ISEVEN", "ISODD"):
            if len(values) != 1:
                return VALUE_ERROR
            value = values[0]
            if name == "N":
                if isinstance(value, bool):
                    return 1 if value else 0
                return value if _numeric(value) else 0
            error, number = _read_number(value)
            if error is not None:
                return error
            # Excel truncates the argument toward zero, the way TRUNC does,
            # and reports on that integer: ISEVEN(-1.5) is ISEVEN(-1).
            return abs(math.trunc(number)) % 2 == (0 if name == "ISEVEN" else 1)
        if name in ("GCD", "LCM"):
            integers: List[int] = []
            for value in values:
                error, number = _read_number(value)
                if error is not None:
                    return error
                truncated = math.trunc(number)
                if truncated < 0 or truncated > MAX_EXACT_INTEGER:
                    return NUM_ERROR
                integers.append(truncated)
            if name == "GCD":
                result = 0
                for value in integers:
                    result = math.gcd(result, value)
                return float(result)
            result = 1
            for value in integers:
                if result == 0 or value == 0:
                    result = 0
                else:
                    # Divide before multiplying so the product stays exactly
                    # representable for as long as possible, as the client does.
                    result = result // math.gcd(result, value) * value
            if result > MAX_EXACT_INTEGER:
                return NUM_ERROR
            return float(result)
        if name == "SUMSQ":
            total = 0.0
            for value in values:
                error, number = _read_number(value)
                if error is not None:
                    return error
                total += number * number
            return total
        if name == "ATAN2":
            if len(values) != 2:
                return VALUE_ERROR
            error, x = _read_number(values[0])
            if error is not None:
                return error
            error, y = _read_number(values[1])
            if error is not None:
                return error
            return math.atan2(x, y)
        if name in ("SINH", "COSH", "TANH"):
            if len(values) != 1:
                return VALUE_ERROR
            error, number = _read_number(values[0])
            if error is not None:
                return error
            try:
                return {
                    "SINH": math.sinh,
                    "COSH": math.cosh,
                    "TANH": math.tanh,
                }[name](number)
            except OverflowError:
                # A hyperbolic sine or cosine past ~710 overflows a double;
                # the raw infinity is not a spreadsheet value.
                return NUM_ERROR
        if name == "TIME":
            if len(values) != 3:
                return VALUE_ERROR
            parts: List[float] = []
            for value in values:
                error, number = _read_number(value)
                if error is not None:
                    return error
                if number < 0 or number > MAX_TIME_COMPONENT:
                    return NUM_ERROR
                parts.append(number)
            # Excel folds the result into a single day, so TIME(27, 0, 0) is
            # 03:00 rather than 27 hours.
            seconds = (parts[0] * 3600 + parts[1] * 60 + parts[2]) % 86400
            return seconds / 86400
        if name == "DATEVALUE":
            if len(values) != 1:
                return VALUE_ERROR
            parsed = _parse_date_text(_to_text(values[0]).strip())
            if parsed is None:
                return VALUE_ERROR
            return float((parsed - _DATE_EPOCH).days)
        if name in ("HOUR", "MINUTE", "SECOND", "SIGN"):
            if len(values) != 1:
                return VALUE_ERROR
            error, number = _read_number(values[0])
            if error is not None:
                return error
            if name == "SIGN":
                if number > 0:
                    return 1
                if number < 0:
                    return -1
                return 0
            seconds = _time_of_day(number)
            if name == "HOUR":
                return float(seconds // 3600)
            if name == "MINUTE":
                return float((seconds % 3600) // 60)
            return float(seconds % 60)
        return VALUE_ERROR

    def _argument_list(
        self, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> List[Any]:
        """Evaluate each argument into its own slot.

        A scalar argument becomes its value, a whole range becomes a flat list
        -- the shape the client engine receives and flattens with ``.flat()``
        -- and an omitted argument becomes a blank, so a caller can test its
        position the way the client tests ``args.length``.
        """
        out: List[Any] = []
        for node in args:
            kind = node[0]
            if kind == "blank":
                out.append(None)
                continue
            if kind == "ref" and ":" in node[1]:
                expanded, _ = self._expand_arg(node, sheet, seen, depth)
                out.append(expanded)
                continue
            out.append(self._eval(node, sheet, seen, depth))
        return out

    def _number_arguments(self, values: List[Any]) -> tuple:
        """Coerce a whole argument list to numbers, as the client's toNumber does.

        Returns ``(error, numbers)``. A range where a scalar belongs is
        #VALUE!, the same answer the client's ``toNumber`` gives an array.
        """
        numbers: List[float] = []
        for value in values:
            error, number = _read_number(value)
            if error is not None:
                return error, None
            numbers.append(number)
        return None, numbers

    def _annuity_payment(
        self, rate: float, nper: float, pv: float, fv: float, type_: float
    ) -> Any:
        """PMT(rate, nper, pv, fv, type), shared with IPMT and PPMT."""
        if nper <= 0:
            return NUM_ERROR
        if rate == 0:
            return _finite(-(fv + pv) / nper)
        temp = _pow_js(1 + rate, nper)
        try:
            return _finite(-(pv * temp + fv) / ((temp - 1) * (1 + rate * type_) / rate))
        except ZeroDivisionError:
            return NUM_ERROR

    def _interest_payment(
        self, rate: float, per: float, nper: float, pv: float, fv: float, type_: float
    ) -> Any:
        """IPMT(rate, per, nper, pv, fv, type): the interest part of a payment."""
        payment = self._annuity_payment(rate, nper, pv, fv, type_)
        if isinstance(payment, str):
            return payment
        if rate == 0:
            return 0.0
        try:
            growth = _pow_js(1 + rate, per - 1)
            balance = pv * growth + payment * (1 + rate * type_) * (growth - 1) / rate
            return _finite(balance * rate)
        except ZeroDivisionError:
            return NUM_ERROR

    def _solve_rate(
        self, nper: float, pmt: float, pv: float, fv: float, type_: float, guess: float
    ) -> Any:
        """RATE: Newton-Raphson on the annuity identity, with a bisection fallback.

        A bad guess can NaN or diverge (Excel's RATE is #NUM! then), so a
        bracketed sign change backs up the iteration; that is what lets the
        ordinary annuity case always converge.
        """
        tolerance = 1e-8

        def residual(rate: float) -> float:
            r1 = 1 + rate
            rn = _pow_js(r1, nper)
            if abs(rate) < 1e-10:
                return pv + pmt * nper + fv
            a = (rn - 1) / rate
            return pv * rn + pmt * (1 + rate * type_) * a + fv

        def derivative(rate: float) -> float:
            r1 = 1 + rate
            rn = _pow_js(r1, nper)
            try:
                a = (rn - 1) / rate
                b = (nper * rn / rate - a) / rate
                return pv * nper * rn / r1 + pmt * (type_ * a + (1 + rate * type_) * b)
            except ZeroDivisionError:
                return float("nan")

        rate = guess
        for _ in range(100):
            # A NaN guess fails this test, exactly as it does in the client.
            if not (rate > -1) or rate > 1e6:
                break
            value = residual(rate)
            if abs(value) < tolerance:
                return _finite(rate)
            slope = derivative(rate)
            if abs(slope) < 1e-12:
                break
            new_rate = rate - value / slope
            if abs(new_rate - rate) < tolerance:
                return _finite(new_rate)
            rate = new_rate

        # Bisection fallback: bracket a root and refine it.
        lo = -0.999999
        hi = max(1.0, abs(guess) * 2)
        f_lo = residual(lo)
        for _ in range(80):
            if not (f_lo * residual(hi) > 0):
                break
            hi *= 2
        if f_lo * residual(hi) <= 0:
            for _ in range(200):
                mid = (lo + hi) / 2
                f_mid = residual(mid)
                if abs(f_mid) < tolerance:
                    return _finite(mid)
                if f_lo * f_mid <= 0:
                    hi = mid
                else:
                    lo = mid
                    f_lo = f_mid
            return _finite((lo + hi) / 2)
        return NUM_ERROR

    def _financial(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """The financial functions: annuities, cash-flow series and depreciation."""
        values = self._argument_list(args, sheet, seen, depth)
        if name in ("PMT", "FV", "PV", "NPER"):
            if len(values) < 3 or len(values) > 5:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            first, second, third = numbers[0], numbers[1], numbers[2]
            fourth = numbers[3] if len(values) >= 4 else 0.0
            fifth = numbers[4] if len(values) == 5 else 0.0
            if name == "PMT":
                return self._annuity_payment(first, second, third, fourth, fifth)
            if name == "FV":
                rate, nper, pmt = first, second, third
                pv, type_ = fourth, fifth
                if rate == 0:
                    return _finite(-(pv + pmt * nper))
                temp = _pow_js(1 + rate, nper)
                return _finite(
                    -(pv * temp + pmt * (1 + rate * type_) * (temp - 1) / rate)
                )
            if name == "PV":
                rate, nper, pmt = first, second, third
                fv, type_ = fourth, fifth
                if rate == 0:
                    return _finite(-(pmt * nper + fv))
                temp = _pow_js(1 + rate, nper)
                try:
                    return _finite(
                        -(pmt * (1 + rate * type_) * (temp - 1) / rate + fv) / temp
                    )
                except ZeroDivisionError:
                    return NUM_ERROR
            # NPER
            rate, pmt, pv = first, second, third
            fv, type_ = fourth, fifth
            if rate == 0:
                if pmt == 0:
                    return DIV_ZERO
                return _finite(-(pv + fv) / pmt)
            numerator = pmt * (1 + rate * type_) - fv * rate
            denominator = pv * rate + pmt * (1 + rate * type_)
            if denominator == 0:
                return DIV_ZERO
            try:
                return _finite(math.log(numerator / denominator) / math.log(1 + rate))
            except (ValueError, ZeroDivisionError):
                return NUM_ERROR
        if name == "RATE":
            if len(values) < 3 or len(values) > 6:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            nper, pmt, pv = numbers[0], numbers[1], numbers[2]
            fv = numbers[3] if len(values) >= 4 else 0.0
            type_ = numbers[4] if len(values) >= 5 else 0.0
            guess = numbers[5] if len(values) == 6 else 0.1
            return self._solve_rate(nper, pmt, pv, fv, type_, guess)
        if name in ("IPMT", "PPMT"):
            if len(values) < 4 or len(values) > 6:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            rate, per, nper, pv = numbers[0], numbers[1], numbers[2], numbers[3]
            fv = numbers[4] if len(values) >= 5 else 0.0
            type_ = numbers[5] if len(values) == 6 else 0.0
            if per < 1 or per > nper:
                return NUM_ERROR
            if name == "IPMT":
                return self._interest_payment(rate, per, nper, pv, fv, type_)
            payment = self._annuity_payment(rate, nper, pv, fv, type_)
            if isinstance(payment, str):
                return payment
            interest = self._interest_payment(rate, per, nper, pv, fv, type_)
            if isinstance(interest, str):
                return interest
            return _finite(payment - interest)
        if name == "NPV":
            if len(values) < 2:
                return VALUE_ERROR
            error, rate = _read_number(values[0])
            if error is not None:
                return error
            cashflows: List[float] = []
            for value in values[1:]:
                if isinstance(value, (list, tuple)):
                    # A range contributes only its numbers; Excel ignores text,
                    # logicals and blanks stored inside a reference.
                    for item in _flatten_argument(value):
                        if _numeric(item):
                            cashflows.append(float(item))
                    continue
                error, number = _read_number(value)
                if error is not None:
                    return error
                cashflows.append(number)
            total = 0.0
            for index, cashflow in enumerate(cashflows):
                total += cashflow / _pow_js(1 + rate, index + 1)
            return _finite(total)
        if name == "IRR":
            if len(values) < 1 or len(values) > 2:
                return VALUE_ERROR
            guess = 0.1
            if len(values) == 2:
                error, guess = _read_number(values[1])
                if error is not None:
                    return error
            error, cashflows = self._cash_flows(values[0])
            if error is not None:
                return error
            if any(value > 0 for value in cashflows) and any(
                value < 0 for value in cashflows
            ):
                rate = guess
                tolerance = 1e-7
                for _ in range(100):
                    npv = 0.0
                    dnpv = 0.0
                    try:
                        for t, cashflow in enumerate(cashflows):
                            npv += cashflow / _pow_js(1 + rate, t)
                            dnpv -= t * cashflow / _pow_js(1 + rate, t + 1)
                    except ZeroDivisionError:
                        return NUM_ERROR
                    if abs(dnpv) < 1e-12:
                        break
                    new_rate = rate - npv / dnpv
                    if abs(new_rate - rate) < tolerance:
                        return _finite(new_rate)
                    rate = new_rate
            return NUM_ERROR
        if name == "MIRR":
            if len(values) != 3:
                return VALUE_ERROR
            error, finance_rate = _read_number(values[1])
            if error is not None:
                return error
            error, reinvest_rate = _read_number(values[2])
            if error is not None:
                return error
            error, cashflows = self._cash_flows(values[0])
            if error is not None:
                return error
            last = len(cashflows) - 1
            if last <= 0:
                return DIV_ZERO
            pv_negative = 0.0
            fv_positive = 0.0
            for t, cashflow in enumerate(cashflows):
                if cashflow < 0:
                    pv_negative += cashflow / _pow_js(1 + finance_rate, t)
                else:
                    fv_positive += cashflow * _pow_js(1 + reinvest_rate, last - t)
            if pv_negative == 0:
                return DIV_ZERO
            return _finite(_pow_js(-fv_positive / pv_negative, 1 / last) - 1)
        if name == "XNPV":
            if len(values) != 3:
                return VALUE_ERROR
            error, rate = _read_number(values[0])
            if error is not None:
                return error
            amounts: List[float] = []
            for item in _flatten_argument(values[1]):
                error, number = _read_number(item)
                if error is not None:
                    return error
                amounts.append(number)
            serials: List[float] = []
            for item in _flatten_argument(values[2]):
                serial = _date_serial(item)
                if serial is None:
                    return VALUE_ERROR
                serials.append(serial)
            if len(amounts) != len(serials):
                return VALUE_ERROR
            start = serials[0]
            total = 0.0
            for amount, serial in zip(amounts, serials):
                total += amount / _pow_js(1 + rate, (serial - start) / 365)
            return _finite(total)
        if name == "SLN":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            cost, salvage, life = numbers
            if life <= 0:
                return NUM_ERROR
            return _finite((cost - salvage) / life)
        if name == "SYD":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            cost, salvage, life, per = numbers
            if life <= 0 or per < 1 or per > life:
                return NUM_ERROR
            denominator = life * (life + 1) / 2
            if denominator == 0:
                return NUM_ERROR
            return _finite((cost - salvage) * (life - per + 1) / denominator)
        if name == "DB":
            if len(values) < 4 or len(values) > 5:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            cost, salvage, life, period = numbers[0], numbers[1], numbers[2], numbers[3]
            month = numbers[4] if len(values) == 5 else 12.0
            if life <= 0 or cost == 0:
                return NUM_ERROR
            rate = 1 - _pow_js(salvage / cost, 1 / life)
            book_value = cost
            depreciation = 0.0
            # ``i`` is compared against the raw period and life, the way the
            # client's loop does, so a fractional period stops at the same
            # iteration on both engines.
            i = 1
            while i <= period:
                if i == 1:
                    depreciation = cost * rate * month / 12
                elif i == life + 1:
                    depreciation = book_value * rate * (12 - month) / 12
                else:
                    depreciation = book_value * rate
                book_value -= depreciation
                i += 1
            return _finite(depreciation)
        return VALUE_ERROR

    def _cash_flows(self, value: Any) -> tuple:
        """A cash-flow range as a list of numbers, as the client's toNumber reads it."""
        cashflows: List[float] = []
        for item in _flatten_argument(value):
            error, number = _read_number(item)
            if error is not None:
                return error, None
            cashflows.append(number)
        return None, cashflows

    def _distribution(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """The probability distributions and their inverses."""
        values = self._argument_list(args, sheet, seen, depth)
        if name == "NORMDIST":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:3])
            if error is not None:
                return error
            x, mean, sd = numbers
            cumulative = self._truthy(values[3])
            if sd <= 0:
                return NUM_ERROR
            z = (x - mean) / sd
            if cumulative:
                return _finite(_gaussian(z))
            return _finite(
                math.exp(-0.5 * _pow_js(z, 2)) / (sd * math.sqrt(2 * math.pi))
            )
        if name == "NORMSDIST":
            if len(values) != 1:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            return _finite(_gaussian(numbers[0]))
        if name in ("NORMINV", "NORMSINV", "NORMINVS"):
            if name == "NORMINV":
                if len(values) != 3:
                    return VALUE_ERROR
                error, numbers = self._number_arguments(values)
                if error is not None:
                    return error
                probability, mean, sd = numbers
            else:
                if len(values) != 1:
                    return VALUE_ERROR
                error, numbers = self._number_arguments(values)
                if error is not None:
                    return error
                probability, mean, sd = numbers[0], 0.0, 1.0
            if probability <= 0 or probability >= 1 or sd <= 0:
                return NUM_ERROR
            return _finite(mean + sd * _inverse_normal(probability))
        if name == "BINOMDIST":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:3])
            if error is not None:
                return error
            successes = math.floor(numbers[0])
            trials = math.floor(numbers[1])
            probability = numbers[2]
            cumulative = self._truthy(values[3])
            if successes < 0 or trials < 0 or probability < 0 or probability > 1:
                return NUM_ERROR
            if successes > trials:
                # Excel reports #NUM! when the successes exceed the trials;
                # answering 0 made a nonsense argument look like a real result.
                return NUM_ERROR
            if cumulative:
                total = 0.0
                for i in range(successes + 1):
                    total += (
                        _combin(trials, i)
                        * _pow_js(probability, i)
                        * _pow_js(1 - probability, trials - i)
                    )
                return _finite(total)
            return _finite(
                _combin(trials, successes)
                * _pow_js(probability, successes)
                * _pow_js(1 - probability, trials - successes)
            )
        if name == "POISSON":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:2])
            if error is not None:
                return error
            events = math.floor(numbers[0])
            mean = numbers[1]
            cumulative = self._truthy(values[2])
            if events < 0 or mean <= 0:
                return NUM_ERROR
            if cumulative:
                total = 0.0
                for i in range(events + 1):
                    total += math.exp(-mean) * _pow_js(mean, i) / _factorial(i)
                return _finite(total)
            return _finite(math.exp(-mean) * _pow_js(mean, events) / _factorial(events))
        if name == "EXPONDIST":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:2])
            if error is not None:
                return error
            x, lambda_ = numbers
            cumulative = self._truthy(values[2])
            if x < 0 or lambda_ <= 0:
                return NUM_ERROR
            if cumulative:
                return _finite(1 - math.exp(-lambda_ * x))
            return _finite(lambda_ * math.exp(-lambda_ * x))
        if name == "WEIBULL":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:3])
            if error is not None:
                return error
            x, alpha, beta = numbers
            cumulative = self._truthy(values[3])
            if x < 0 or alpha <= 0 or beta <= 0:
                return NUM_ERROR
            if cumulative:
                return _finite(1 - math.exp(-_pow_js(x / beta, alpha)))
            return _finite(
                alpha
                / beta
                * _pow_js(x / beta, alpha - 1)
                * math.exp(-_pow_js(x / beta, alpha))
            )
        if name == "GAMMALN":
            if len(values) != 1:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            if numbers[0] <= 0:
                return NUM_ERROR
            return _finite(_gammaln(numbers[0]))
        if name == "GAMMADIST":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:3])
            if error is not None:
                return error
            x, alpha, beta = numbers
            cumulative = self._truthy(values[3])
            if x < 0 or alpha <= 0 or beta <= 0:
                return NUM_ERROR
            if cumulative:
                return _finite(_gamma_p(alpha, x / beta))
            if x == 0:
                return _finite((1 / beta) if alpha == 1 else 0.0)
            return _finite(
                math.exp(
                    -(x / beta) + (alpha - 1) * math.log(x / beta) - _gammaln(alpha)
                )
                / beta
            )
        if name == "GAMMAINV":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            probability, alpha, beta = numbers
            if probability <= 0 or probability >= 1 or alpha <= 0 or beta <= 0:
                return NUM_ERROR
            lo = 0.0
            hi = beta * max(1.0, alpha)
            guard = 0
            while _gamma_p(alpha, hi / beta) < probability and guard < 60:
                hi *= 2
                guard += 1
            if guard >= 60:
                return NUM_ERROR
            for _ in range(100):
                mid = (lo + hi) / 2
                if _gamma_p(alpha, mid / beta) < probability:
                    lo = mid
                else:
                    hi = mid
                if hi - lo < 1e-10 * max(1.0, abs(mid)):
                    break
            return _finite((lo + hi) / 2)
        if name in ("BETADIST", "BETAINV"):
            if len(values) < 3 or len(values) > 5:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            first, alpha, beta = numbers[0], numbers[1], numbers[2]
            a = numbers[3] if len(values) >= 4 else 0.0
            b = numbers[4] if len(values) >= 5 else 1.0
            if name == "BETADIST":
                if alpha <= 0 or beta <= 0 or b <= a:
                    return NUM_ERROR
                t = (first - a) / (b - a)
                if t <= 0:
                    return 0
                if t >= 1:
                    return 1
                return _finite(_betai(t, alpha, beta))
            if first <= 0 or first >= 1 or alpha <= 0 or beta <= 0 or b <= a:
                return NUM_ERROR
            lo, hi = 0.0, 1.0
            for _ in range(100):
                mid = (lo + hi) / 2
                if _betai(mid, alpha, beta) < first:
                    lo = mid
                else:
                    hi = mid
                if hi - lo < 1e-12:
                    break
            return _finite(a + (b - a) * ((lo + hi) / 2))
        if name == "NEGBINOMDIST":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            failures = math.floor(numbers[0])
            successes = math.floor(numbers[1])
            probability = numbers[2]
            if failures < 0 or successes < 1 or probability < 0 or probability > 1:
                return NUM_ERROR
            return _finite(
                _combin(failures + successes - 1, successes - 1)
                * _pow_js(probability, successes)
                * _pow_js(1 - probability, failures)
            )
        if name == "FREQUENCY":
            if len(values) != 2:
                return VALUE_ERROR
            data = _number_values(_flatten_argument(values[0]))
            bins = _number_values(_flatten_argument(values[1]))
            result = [0] * (len(bins) + 1)
            for value in data:
                for index, edge in enumerate(bins):
                    if value <= edge:
                        result[index] += 1
                        break
                else:
                    result[-1] += 1
            return result
        return VALUE_ERROR

    def _combinatorics(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """The counting functions: COMBIN/COMBINA/PERMUT, FACT/FACTDOUBLE and
        MULTINOMIAL."""
        values = self._argument_list(args, sheet, seen, depth)
        if name in ("COMBIN", "COMBINA", "PERMUT"):
            if len(values) != 2:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            n = math.floor(numbers[0])
            k = math.floor(numbers[1])
            if n < 0 or k < 0:
                return NUM_ERROR
            if name == "COMBINA":
                return _finite(_combin(n + k - 1, k))
            if k > n:
                return NUM_ERROR
            if name == "COMBIN":
                return _finite(_combin(n, k))
            result = 1.0
            for i in range(k):
                result *= n - i
                if not math.isfinite(result):
                    return NUM_ERROR
            return result
        if name in ("FACT", "FACTDOUBLE"):
            if len(values) != 1:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            n = math.floor(numbers[0])
            if n < 0:
                return NUM_ERROR
            if name == "FACT":
                return _finite(_factorial(n))
            result = 1.0
            i = n
            while i > 1:
                result *= i
                if not math.isfinite(result):
                    return NUM_ERROR
                i -= 2
            return result
        if name == "MULTINOMIAL":
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            integers = [math.floor(number) for number in numbers]
            if any(value < 0 for value in integers):
                return NUM_ERROR
            result = _factorial(sum(integers))
            for value in integers:
                result /= _factorial(value)
            if not math.isfinite(result):
                return NUM_ERROR
            return float(math.floor(result + 0.5))
        return VALUE_ERROR
