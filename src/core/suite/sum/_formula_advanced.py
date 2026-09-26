"""Formula evaluator mixin: advanced text/date/trig/criteria functions."""

import datetime
import math
from typing import Any, List, Optional

from ._formula_core import FormulaEvaluatorCore
from ._formula_shared import (
    DIV_ZERO,
    ERROR_CODES,
    MAX_DATE_SERIAL,
    MAX_LOOP_ITERATIONS,
    MIN_DATE_SERIAL,
    MAX_NETWORKDAYS_SPAN_DAYS,
    MAX_REPT_CHARS,
    MAX_WORKDAY_OFFSET_DAYS,
    NAME_ERROR,
    N_A_ERROR,
    NUMERIC_TEXT_RE,
    NUM_ERROR,
    REF_ERROR,
    VALUE_ERROR,
    _DATE_EPOCH,
    _compare_values,
    _computed_range_error,
    _js_number_text,
    _numeric,
    _to_number,
    _to_number_or_zero,
    _to_text,
    _text_equals,
    _utf16_find,
    _utf16_first_unit,
    _utf16_len,
    _utf16_slice,
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


def _date_part(serial: float) -> Optional[int]:
    """The date a serial names, or ``None`` when it names none.

    The serial's integer part (truncated toward zero, matching the client
    engine's ``Math.trunc``) is the date and its fraction is a time of day, so
    the range is checked on the part, not on the raw serial: ``2958465.5``
    names 9999-12-31 and is in range. A serial whose date part falls outside
    the covered span is ``None``, which the caller reports as #NUM!.
    """
    if not math.isfinite(serial):
        return None
    part = math.trunc(serial)
    if part < MIN_DATE_SERIAL or part > MAX_DATE_SERIAL:
        return None
    return part


def _time_of_day(serial: float) -> int:
    """The seconds past midnight the serial names.

    Read from the whole milliseconds the serial carries, exactly as the
    client engine's Date does: ``Date`` holds an integer millisecond count, so
    the time of day is ``trunc(serial * 86400000)`` reduced to one day. A
    serial naming 41.8 seconds reads 41, and truncating toward the second
    (rather than rounding) is what the client -- and Excel -- answer.

    This used to subtract ``floor(serial)`` and read the leftover fraction,
    which rounds differently at the edges: for a negative serial the two
    spellings disagreed by a second near a millisecond boundary
    (``SECOND(-78320.75783565)`` was 43 on the client and 42 here).
    """
    total_ms = math.trunc(serial * 86400000.0)
    return (total_ms % 86400000) // 1000 % 86400


def _read_number(value: Any) -> tuple:
    """Coerce one value, returning ``(error, number)``.

    A function that cannot skip a value (SUMSQ, the cross-product sums, a
    rank or a percentile) reports the coercion failure instead of treating the
    value as absent, which is what the client engine's ``toNumber`` does.

    The scalar slot follows the same pooled-argument rule as the client's
    ``scalarize``: an error is not a value to coerce but one to propagate --
    ``PMT(1/0, 1/0, 1/0)`` is #DIV/0!, not the #VALUE! a failed coercion would
    give -- and a range read where one value belongs answers #VALUE! unless it
    holds exactly one cell, that cell's error propagating first. Without the
    explicit check ``_to_number`` treated the error text as ordinary text and
    mangled every operand error into #VALUE!.
    """
    if is_error(value):
        return value, None
    if isinstance(value, (list, tuple)):
        cells = _flatten_argument(value)
        for cell in cells:
            if is_error(cell):
                return cell, None
        if len(cells) != 1:
            return VALUE_ERROR, None
        value = cells[0]
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
    if isinstance(left, str):
        # Two text values compare the way ``=`` does, which ignores case:
        # SWITCH("b", "a", 1, "B", 2) is 2, not the default.
        return _text_equals(left, right)
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
    # JavaScript divides by zero to Infinity rather than raising, and the
    # continued fraction then carries that Infinity into a NaN. Reproduce it
    # there instead of letting the ZeroDivisionError escape: at huge `a` the
    # ``1 - a`` rounds away, so ``b`` is exactly zero and the client answers
    # #NUM! while this engine used to raise.
    d = 1.0 / b if b != 0 else float("inf")
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


# Excel's complex-number text form, shared with the client engine. A value is
# ``a+bi`` (or ``j``), a bare real is ``a+0i``, and a trailing-only imaginary
# term like ``4i`` carries its own coefficient, so the parse splits on the last
# joining sign that is neither a leading sign nor an exponent sign.
def _parse_complex(value: Any):
    """Parse Excel complex text into ``(re, im, suffix)``, or ``None``.

    ``None`` means the text is not a complex number, which the caller turns
    into ``#NUM!``.
    """
    text = _to_text(value).strip()
    if text == "":
        return None
    unit = text[-1]
    if unit not in ("i", "j"):
        if not NUMERIC_TEXT_RE.match(text):
            return None
        return float(text), 0.0, "i"
    body = text[:-1]
    if body in ("", "+"):
        return 0.0, 1.0, unit
    if body == "-":
        return 0.0, -1.0, unit
    split = -1
    for index in range(len(body) - 1, 0, -1):
        if body[index] in "+-" and body[index - 1] not in "eE":
            split = index
            break
    if split == -1:
        if not NUMERIC_TEXT_RE.match(body):
            return None
        return 0.0, float(body), unit
    real_part = body[:split]
    imaginary_part = body[split:]
    if not NUMERIC_TEXT_RE.match(real_part):
        return None
    if imaginary_part == "+":
        return float(real_part), 1.0, unit
    if imaginary_part == "-":
        return float(real_part), -1.0, unit
    if not NUMERIC_TEXT_RE.match(imaginary_part):
        return None
    return float(real_part), float(imaginary_part), unit


def _format_coefficient(value: float) -> str:
    """Print a coefficient as Excel does: 15 significant digits, no trailing
    zeros. This reproduces JavaScript's ``toPrecision(15)``, which the client
    engine uses, so the two engines emit byte-identical text."""
    if value == 0:
        return "0"
    text = f"{value:.14e}"
    mantissa, _, exponent = text.partition("e")
    exponent_value = int(exponent)
    digits = mantissa.replace("-", "").replace(".", "")
    sign = "-" if mantissa.startswith("-") else ""
    if exponent_value < -6 or exponent_value >= 15:
        rest = digits[1:].rstrip("0")
        mantissa_text = digits[0] + ("." + rest if rest else "")
        exponent_sign = "+" if exponent_value >= 0 else "-"
        return f"{sign}{mantissa_text}e{exponent_sign}{abs(exponent_value)}"
    if exponent_value >= 0:
        integer_part = digits[: exponent_value + 1]
        fraction = digits[exponent_value + 1 :].rstrip("0")
    else:
        integer_part = "0"
        fraction = ("0" * (-exponent_value - 1) + digits).rstrip("0")
    return f"{sign}{integer_part}" + (f".{fraction}" if fraction else "")


def _format_complex(real: float, imaginary: float, suffix: str = "i") -> str:
    """Render coefficients in Excel's complex-number text form.

    A coefficient a double cannot hold is not a complex value, so it is
    ``#NUM!`` -- the same rule ``SINH``/``COSH`` follow for an argument past
    ~710. Printing it instead produced text like ``Infinity-NaNi``.
    """
    if not (math.isfinite(real) and math.isfinite(imaginary)):
        return NUM_ERROR
    result = ""
    if real != 0:
        result += _format_coefficient(real)
    if imaginary != 0:
        if imaginary > 0 and real != 0:
            sign = "+"
        elif imaginary > 0:
            sign = ""
        else:
            sign = "-"
        magnitude = _format_coefficient(abs(imaginary))
        result += sign + ("" if magnitude == "1" else magnitude) + suffix
    return result if result != "" else "0"


def _overflowing(function, value: float) -> float:
    """A hyperbolic or exponential value, as the double that holds it.

    Python's ``math`` raises ``OverflowError`` where JavaScript quietly answers
    ``Infinity`` (``IMSINH(1e300)`` used to escape the evaluator as an
    exception). The finiteness check in ``_complex`` is what turns either into
    ``#NUM!``, so the two engines answer the same thing and no formula can
    raise its way out of the sheet.
    """
    try:
        return function(value)
    except OverflowError:
        return math.inf


def _complex_sin(value):
    return (
        math.sin(value[0]) * _overflowing(math.cosh, value[1]),
        math.cos(value[0]) * _overflowing(math.sinh, value[1]),
    )


def _complex_cos(value):
    return (
        math.cos(value[0]) * _overflowing(math.cosh, value[1]),
        -math.sin(value[0]) * _overflowing(math.sinh, value[1]),
    )


def _complex_sinh(value):
    return (
        _overflowing(math.sinh, value[0]) * math.cos(value[1]),
        _overflowing(math.cosh, value[0]) * math.sin(value[1]),
    )


def _complex_cosh(value):
    return (
        _overflowing(math.cosh, value[0]) * math.cos(value[1]),
        _overflowing(math.sinh, value[0]) * math.sin(value[1]),
    )


def _complex_divide(numerator, denominator):
    """Complex division, or ``None`` when the divisor is zero."""
    modulus = denominator[0] * denominator[0] + denominator[1] * denominator[1]
    if modulus == 0:
        return None
    return (
        (numerator[0] * denominator[0] + numerator[1] * denominator[1]) / modulus,
        (numerator[1] * denominator[0] - numerator[0] * denominator[1]) / modulus,
    )


# ── plumbing for the engineering set ──
#
# The base conversions, bitwise operations, DELTA/GESTEP, ERF/ERFC, the Bessel
# functions and CONVERT, ported from the client engine's engineering module and
# kept identical down to its quirks, so a sheet that uses them reads the same
# whichever engine evaluates it.


def _js_string(value: Any) -> str:
    """JavaScript's ``String(value)`` for a cell value.

    A blank is ``"null"`` rather than the empty text the sheet helpers use, a
    logical keeps its lowercase spelling, and a number renders as JavaScript
    prints it -- which is what the conversions and CONVERT feed their parsing
    and unit lookup, so ``BIN2DEC`` of a blank is ``#NUM!`` rather than 0.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _js_number_text(float(value))
    return str(value)


def _parse_base(value: Any, base: int) -> Optional[int]:
    """Read base-``base`` text the way the conversions do, or ``None``.

    ``None`` means the text is not a number in that base, which the caller
    turns into ``#NUM!``. The input is trimmed, upper-cased and stripped of a
    single ``0X`` prefix; an empty remainder reads as 0.
    """
    text = _js_string(value).strip().upper()
    if text.startswith("0X"):
        text = text[2:]
    result = 0
    for char in text:
        try:
            digit = int(char, base)
        except ValueError:
            return None
        result = result * base + digit
    return result


def _to_base(value: float, base: int, places: Optional[float]) -> Any:
    """Print a number in base ``base``, padded to ``places`` when given.

    A negative value is rendered in two's complement with at least 10 digits,
    exactly as the client engine sizes it; a result longer than ``places`` is
    ``#NUM!``. ``places`` counts characters the way ``padStart`` does, so a
    fractional count is floored by the padding itself.
    """
    if value < 0:
        # Two's complement in 10 digits, exactly as the client engine sizes
        # it: every negative conversion passes an explicit 10-digit width, so
        # the computed minimum never applies -- even a small magnitude like
        # -512 renders in the full 10-digit form.
        value = float(base**10) + value
    result = ""
    remaining = abs(value)
    if remaining == 0:
        result = "0"
    while remaining > 0:
        digit = remaining % base
        if digit < 10:
            result = _js_number_text(digit) + result
        else:
            result = chr(55 + int(digit)) + result
        remaining = math.floor(remaining / base)
    if places is not None:
        if len(result) > places:
            return NUM_ERROR
        try:
            result = result.rjust(int(places), "0")
        except (MemoryError, OverflowError):
            # A padding no string can hold: the client engine's ``padStart``
            # throws there too, which its evaluator reports as ``#VALUE!``.
            return VALUE_ERROR
    return result


def _fact_float(n: int) -> float:
    """``n!`` accumulated in doubles, the way the client series does.

    The factorials feed a division, so an exactly-rounded integer factorial
    divided once can differ from the client's multiply-and-divide chain in the
    last ulp; accumulating here keeps the Bessel terms bit-identical.
    """
    result = 1.0
    for index in range(2, n + 1):
        result *= index
        if not math.isfinite(result):
            # A factorial past 170 leaves the doubles. Returning as soon as it
            # overflows keeps a huge order from spinning a loop that cannot
            # produce a representable number; the loop below would answer the
            # same infinity.
            return float("inf")
    return result


def _erf_single(x: float) -> float:
    """The error function (Abramowitz & Stegun 7.1.26), as the client runs it."""
    sign = -1.0 if x < 0 else 1.0
    ax = abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    y = 1.0 - (
        ((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
        + 0.254829592
    ) * t * math.exp(-ax * ax)
    return sign * y


def _bessel_j(x: float, n: int) -> float:
    """The Bessel function of the first kind ``J_n(x)``, by series."""
    if x == 0:
        return 1.0 if n == 0 else 0.0
    total = 0.0
    for k in range(40):
        term = (
            (-1) ** k / (_fact_float(k) * _fact_float(k + n)) * (x / 2) ** (2 * k + n)
        )
        total += term
        if abs(term) < 1e-14:
            break
    return total


def _bessel_y0_series(x: float) -> float:
    """``Y_0(x)`` by the Abramowitz & Stegun 9.1.13 series, as the client runs it."""
    gamma = 0.5772156649015329
    result = (2 / math.pi) * (math.log(x / 2) + gamma) * _bessel_j(x, 0)
    for k in range(1, 40):
        harmonic = 0.0
        for j in range(1, k + 1):
            harmonic += 1 / j
        term = (
            (-1) ** (k - 1)
            * harmonic
            / (_fact_float(k) * _fact_float(k))
            * (x / 2) ** (2 * k)
        )
        result += (2 / math.pi) * term
        if k > 2 and abs(term) < 1e-16:
            break
    return result


# CONVERT's unit table: each entry is ``(dimension, factor-to-base)``. Ported
# entry-for-entry from the client engine, including the Excel-compat fixes its
# comments describe (the ``at`` atmosphere, the ``e`` erg, the ``cal`` IT
# calorie, the ``m/h`` metre-per-hour and the decimal/binary data units).
_CONVERT_UNITS = {
    "M": ("length", 1),
    "METER": ("length", 1),
    "METERS": ("length", 1),
    "METRE": ("length", 1),
    "CM": ("length", 0.01),
    "CENTIMETER": ("length", 0.01),
    "CENTIMETERS": ("length", 0.01),
    "MM": ("length", 0.001),
    "MILLIMETER": ("length", 0.001),
    "MILLIMETERS": ("length", 0.001),
    "KM": ("length", 1000),
    "KILOMETER": ("length", 1000),
    "KILOMETERS": ("length", 1000),
    "IN": ("length", 0.0254),
    "INCH": ("length", 0.0254),
    "INCHES": ("length", 0.0254),
    "FT": ("length", 0.3048),
    "FOOT": ("length", 0.3048),
    "FEET": ("length", 0.3048),
    "YD": ("length", 0.9144),
    "YARD": ("length", 0.9144),
    "YARDS": ("length", 0.9144),
    "MI": ("length", 1609.344),
    "MILE": ("length", 1609.344),
    "MILES": ("length", 1609.344),
    "NMI": ("length", 1852),
    "NAUTMI": ("length", 1852),
    "G": ("mass", 1),
    "GRAM": ("mass", 1),
    "GRAMS": ("mass", 1),
    "GR": ("mass", 1),
    "KG": ("mass", 1000),
    "KILOGRAM": ("mass", 1000),
    "KILOGRAMS": ("mass", 1000),
    "MG": ("mass", 0.001),
    "MILLIGRAM": ("mass", 0.001),
    "MILLIGRAMS": ("mass", 0.001),
    "LB": ("mass", 453.59237),
    "LBM": ("mass", 453.59237),
    "POUND": ("mass", 453.59237),
    "POUNDS": ("mass", 453.59237),
    "OZ": ("mass", 28.349523125),
    "OUNCE": ("mass", 28.349523125),
    "OUNCES": ("mass", 28.349523125),
    "STONE": ("mass", 6350.29318),
    "TON": ("mass", 907184.74),
    "UK_TON": ("mass", 1016046.9088),
    "LTON": ("mass", 1016046.9088),
    "MTON": ("mass", 1000000),
    "SEC": ("time", 1),
    "SECOND": ("time", 1),
    "SECONDS": ("time", 1),
    "S": ("time", 1),
    "MIN": ("time", 60),
    "MINUTE": ("time", 60),
    "MINUTES": ("time", 60),
    "MN": ("time", 60),
    "HR": ("time", 3600),
    "HOUR": ("time", 3600),
    "HOURS": ("time", 3600),
    "DAY": ("time", 86400),
    "DAYS": ("time", 86400),
    "DY": ("time", 86400),
    "WK": ("time", 604800),
    "WEEK": ("time", 604800),
    "WEEKS": ("time", 604800),
    "YR": ("time", 31536000),
    "YEAR": ("time", 31536000),
    "YEARS": ("time", 31536000),
    "L": ("volume", 1),
    "LITER": ("volume", 1),
    "LITERS": ("volume", 1),
    "LTR": ("volume", 1),
    "ML": ("volume", 0.001),
    "MILLILITER": ("volume", 0.001),
    "MILLILITERS": ("volume", 0.001),
    "CUP": ("volume", 0.2365882365),
    "CUPS": ("volume", 0.2365882365),
    "GAL": ("volume", 3.785411784),
    "GALLON": ("volume", 3.785411784),
    "GALLONS": ("volume", 3.785411784),
    "QT": ("volume", 0.946352946),
    "QUART": ("volume", 0.946352946),
    "QUARTS": ("volume", 0.946352946),
    "PT": ("volume", 0.473176473),
    "PINT": ("volume", 0.473176473),
    "PINTS": ("volume", 0.473176473),
    "TSP": ("volume", 0.00492892159375),
    "TEASPOON": ("volume", 0.00492892159375),
    "TBSP": ("volume", 0.01478676478125),
    "TABLESPOON": ("volume", 0.01478676478125),
    "FL_OZ": ("volume", 0.0295735295625),
    "OZ_FL": ("volume", 0.0295735295625),
    "PA": ("pressure", 1),
    "PASCAL": ("pressure", 1),
    "ATM": ("pressure", 101325),
    "ATMOSPHERE": ("pressure", 101325),
    "MMHG": ("pressure", 133.322387415),
    "PSI": ("pressure", 6894.757293168),
    "BAR": ("pressure", 100000),
    "AT": ("pressure", 101325),
    "N": ("force", 1),
    "NEWTON": ("force", 1),
    "NEWTONS": ("force", 1),
    "DYN": ("force", 0.00001),
    "DYNE": ("force", 0.00001),
    "DYNES": ("force", 0.00001),
    "LBF": ("force", 4.4482216152605),
    "POUND_FORCE": ("force", 4.4482216152605),
    "J": ("energy", 1),
    "JOULE": ("energy", 1),
    "JOULES": ("energy", 1),
    "E": ("energy", 1e-7),
    "ERG": ("energy", 1e-7),
    "ERGS": ("energy", 1e-7),
    "CAL": ("energy", 4.1868),
    "CALORIE": ("energy", 4.1868),
    "CALORIES": ("energy", 4.1868),
    "KWH": ("energy", 3600000),
    "BTU": ("energy", 1055.05585262),
    "EV": ("energy", 1.602176634e-19),
    "ELECTRONVOLT": ("energy", 1.602176634e-19),
    "W": ("power", 1),
    "WATT": ("power", 1),
    "WATTS": ("power", 1),
    "HP": ("power", 745.6998715822702),
    "HORSEPOWER": ("power", 745.6998715822702),
    "C": ("temperature", None),
    "CELSIUS": ("temperature", None),
    "CEL": ("temperature", None),
    "F": ("temperature", None),
    "FAHRENHEIT": ("temperature", None),
    "FAH": ("temperature", None),
    "K": ("temperature", None),
    "KELVIN": ("temperature", None),
    "KEL": ("temperature", None),
    "M/H": ("speed", 1 / 3600),
    "M/HR": ("speed", 1 / 3600),
    "MPH": ("speed", 0.44704),
    "MI/H": ("speed", 0.44704),
    "KM/H": ("speed", 0.2777777778),
    "KMH": ("speed", 0.2777777778),
    "KPH": ("speed", 0.2777777778),
    "KN": ("speed", 0.5144444444),
    "KNOT": ("speed", 0.5144444444),
    "KNOTS": ("speed", 0.5144444444),
    "M/S": ("speed", 1),
    "MPS": ("speed", 1),
    "M/SEC": ("speed", 1),
    "FT/S": ("speed", 0.3048),
    "FPS": ("speed", 0.3048),
    "BIT": ("data", 1),
    "BITS": ("data", 1),
    "BYTE": ("data", 8),
    "BYTES": ("data", 8),
    "B": ("data", 8),
    "KB": ("data", 8000),
    "KIB": ("data", 8192),
    "KIBIBYTE": ("data", 8192),
    "MB": ("data", 8000000),
    "MIB": ("data", 8388608),
    "MIBIBYTE": ("data", 8388608),
    "GB": ("data", 8000000000),
    "GIB": ("data", 8589934592),
    "GIBIBYTE": ("data", 8589934592),
    "TB": ("data", 8000000000000),
    "TIB": ("data", 8796093022208),
    "TEBIBYTE": ("data", 8796093022208),
    "KBIT": ("data", 1000),
    "KIBIT": ("data", 1024),
    "MBIT": ("data", 1000000),
    "MIBIT": ("data", 1048576),
    "GBIT": ("data", 1000000000),
    "GIBIT": ("data", 1073741824),
    "TBIT": ("data", 1000000000000),
    "TIBIT": ("data", 1099511627776),
}


def _to_int32(number: float) -> int:
    """JavaScript's ``ToInt32`` for the bitwise operations.

    The client engine evaluates ``BITAND``/``BITOR``/``BITXOR`` with the
    ``&``/``|``/``^`` operators, which wrap past 2**31 (so a large input can
    answer negative), where Python's integers never wrap.
    """
    wrapped = int(number) % 2**32
    return wrapped - 2**32 if wrapped >= 2**31 else wrapped


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
        """INDEX, MATCH, INDIRECT, OFFSET, LOOKUP, XLOOKUP."""
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
            row_value = self._eval(args[1], sheet, seen, depth)
            if is_error(row_value):
                return row_value
            row_num = int(_to_number(row_value))
            col_num = 1
            if len(args) == 3:
                col_value = self._eval(args[2], sheet, seen, depth)
                if is_error(col_value):
                    return col_value
                col_num = int(_to_number(col_value))
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
            if is_error(lookup_value):
                return lookup_value
            matrix = self._range_matrix(args[1], sheet, seen, depth)
            if not matrix:
                return N_A_ERROR
            match_type = 1
            if len(args) == 3:
                type_value = self._eval(args[2], sheet, seen, depth)
                if is_error(type_value):
                    return type_value
                match_type = int(_to_number(type_value))
            # Flatten matrix for matching
            flat = [row[0] if row else None for row in matrix]
            if match_type == 0:  # Exact match
                # A wildcard match is what Excel calls an exact match here:
                # MATCH("ap*", D1:D3, 0) finds "apple".
                for i, val in enumerate(flat):
                    if self._lookup_eq(val, lookup_value, True):
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
            ref_value = self._eval(args[0], sheet, seen, depth)
            if is_error(ref_value):
                return ref_value
            ref_text = _to_text(ref_value)
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
            rows_value = self._eval(args[1], sheet, seen, depth)
            if is_error(rows_value):
                return rows_value
            cols_value = self._eval(args[2], sheet, seen, depth)
            if is_error(cols_value):
                return cols_value
            rows = int(_to_number(rows_value))
            cols = int(_to_number(cols_value))
            height = r2 - r1 + 1
            width = c2 - c1 + 1
            if len(args) >= 4:
                height_value = self._eval(args[3], sheet, seen, depth)
                if is_error(height_value):
                    return height_value
                height = int(_to_number(height_value))
            if len(args) == 5:
                width_value = self._eval(args[4], sheet, seen, depth)
                if is_error(width_value):
                    return width_value
                width = int(_to_number(width_value))
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
            if is_error(lookup_value):
                return lookup_value
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
        elif name == "XLOOKUP":
            # XLOOKUP(lookup_value, lookup_array, return_array, [if_not_found],
            #         [match_mode], [search_mode])
            # match_mode: 0 exact (default), -1 exact or next smaller,
            #             1 exact or next larger, 2 wildcard (*, ?, ~).
            # search_mode: 1 first-to-last (default), -1 last-to-first,
            #              2 binary ascending, -2 binary descending.
            # The binary modes scan linearly in the same direction here: on
            # sorted data the answers agree, and this engine never promises
            # logarithmic time, only the same value.
            if len(args) < 3 or len(args) > 6:
                return VALUE_ERROR
            lookup_value = self._eval(args[0], sheet, seen, depth)
            if is_error(lookup_value):
                return lookup_value
            lookup_matrix = self._range_matrix(args[1], sheet, seen, depth)
            if not lookup_matrix:
                value = self._eval(args[1], sheet, seen, depth)
                if is_error(value):
                    return value
                lookup_matrix = [list(value)] if isinstance(value, list) else [[value]]
            return_matrix = self._range_matrix(args[2], sheet, seen, depth)
            if not return_matrix:
                value = self._eval(args[2], sheet, seen, depth)
                if is_error(value):
                    return value
                return_matrix = [list(value)] if isinstance(value, list) else [[value]]
            match_mode = 0
            if len(args) >= 5:
                mode_value = self._eval(args[4], sheet, seen, depth)
                if is_error(mode_value):
                    return mode_value
                try:
                    match_mode = int(_to_number(mode_value))
                except ValueError as exc:
                    return str(exc)
            search_mode = 1
            if len(args) == 6:
                search_value = self._eval(args[5], sheet, seen, depth)
                if is_error(search_value):
                    return search_value
                try:
                    search_mode = int(_to_number(search_value))
                except ValueError as exc:
                    return str(exc)
            if match_mode not in (0, -1, 1, 2):
                return VALUE_ERROR
            if search_mode not in (1, -1, 2, -2):
                return VALUE_ERROR
            # A single row is a horizontal vector; anything taller is
            # searched by its first column, the way VLOOKUP keys on the
            # first column of its table.
            if len(lookup_matrix) == 1:
                lookup_vector = list(lookup_matrix[0])
            else:
                lookup_vector = [row[0] if row else None for row in lookup_matrix]
            # The return block answers row-wise when it has one row per
            # lookup entry, and column-wise when both blocks are a single
            # row of the same length (the horizontal form).
            column_wise = False
            if len(return_matrix) == len(lookup_vector):
                return_rows = [list(row) for row in return_matrix]
            elif (
                len(lookup_matrix) == 1
                and len(return_matrix) == 1
                and len(return_matrix[0]) == len(lookup_vector)
            ):
                column_wise = True
                return_rows = [list(return_matrix[0])]
            else:
                return VALUE_ERROR
            if search_mode in (-1, -2):
                order = list(range(len(lookup_vector) - 1, -1, -1))
            else:
                order = list(range(len(lookup_vector)))
            matched = -1
            # The approximate modes answer the *closest* value on the right
            # side -- the smallest entry ranked at or above the lookup for
            # mode 1, the largest at or below it for mode -1 -- rather than
            # the first entry the scan crosses. A forward scan that stopped
            # at the first entry <= the lookup answered 1 for
            # XLOOKUP(2.5, {1,2,3}, ..., -1) where the next smaller is 2.
            # Ties keep the first entry in search order.
            best_probe: Any = None
            for i in order:
                probe = lookup_vector[i]
                if match_mode == 0:
                    if self._lookup_eq(probe, lookup_value):
                        matched = i
                        break
                elif match_mode == 2:
                    if self._lookup_eq(probe, lookup_value, True):
                        matched = i
                        break
                elif match_mode == 1:
                    # Exact or next larger: the first entry that ranks at
                    # or above the lookup value. Ranked comparison (not
                    # numeric coercion) is what lets text order too.
                    if _compare_values(probe, lookup_value) >= 0 and (
                        matched < 0 or _compare_values(probe, best_probe) < 0
                    ):
                        matched = i
                        best_probe = probe
                else:
                    if _compare_values(probe, lookup_value) <= 0 and (
                        matched < 0 or _compare_values(probe, best_probe) > 0
                    ):
                        matched = i
                        best_probe = probe
            if matched < 0:
                # if_not_found evaluates only on a miss, so a costly or
                # failing fallback never poisons a hit.
                if len(args) >= 4:
                    return self._eval(args[3], sheet, seen, depth)
                return N_A_ERROR
            if column_wise:
                return return_rows[0][matched]
            row = return_rows[matched]
            if len(row) == 1:
                return row[0]
            return list(row)
        return VALUE_ERROR

    # ── Advanced aggregate functions ────────────────────────────────────────

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
        """TEXT, VALUE, NUMBERVALUE, FIND, SEARCH, REPLACE, REPT, CHAR, CODE."""
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
            find_text = _to_text(values[0]).lower()
            within_text = _to_text(values[1]).lower()
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
            old_text = _to_text(values[0])
            start_number = _to_number(values[1])
            count_number = _to_number(values[2])
            new_text = _to_text(values[3])
            if start_number < 1:
                return VALUE_ERROR
            # Unit-based like the slices: cutting inside an astral character
            # keeps the surviving half, as Excel and the client engine do.
            #
            # The cut is the client's ``start_num - 1 + num_chars`` truncated
            # as one sum, not each part truncated first: JavaScript truncates
            # the slice bound, so a negative fractional count shortens the
            # tail rather than collapsing to zero.
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
            text = _to_text(values[0])
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
                # The last day of the month, from the month lengths directly:
                # building the first of the *next* month and stepping back one
                # day ran off the end of December 9999 (there is no
                # 10000-01-01) and answered #NUM! for EOMONTH(2958465, 0).
                leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
                last_day = [
                    31,
                    29 if leap else 28,
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
                ][month - 1]
                end_date = datetime.date(year, month, last_day)
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
                number = _to_number(values[0])
                return_type = int(_to_number(values[1])) if len(values) == 2 else 1
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            serial = _date_part(number)
            if serial is None:
                return NUM_ERROR
            date_val = _DATE_EPOCH + datetime.timedelta(days=serial)
            # Type 21 is ISO-8601: week 1 holds the year's first Thursday.
            if return_type == 21:
                return float(date_val.isocalendar()[1])
            start_day = _WEEK_START_DAYS.get(return_type)
            if start_day is None:
                return NUM_ERROR
            # Every other type numbers from the week containing January 1 of
            # the date's own year, differing only in the first day of the
            # week. ``isoweekday() % 7`` re-indexes Monday..Sunday onto
            # Sunday-first 0..6 to match ``start_day``. The week start is
            # subtracted as an integer day ordinal rather than as a date:
            # the week holding a January 1 can begin in the previous year (or
            # before 0001-01-01 for a year-1 date), and a timedelta there left
            # the representable range and escaped as an OverflowError.
            day_ord = date_val.toordinal()
            jan1 = datetime.date(date_val.year, 1, 1)
            jan1_ord = jan1.toordinal()
            week_start = day_ord - ((date_val.isoweekday() % 7 - start_day) % 7)
            first_week_start = jan1_ord - ((jan1.isoweekday() % 7 - start_day) % 7)
            return float((week_start - first_week_start) // 7 + 1)
        elif name == "NETWORKDAYS":
            # NETWORKDAYS(start_date, end_date, [holidays])
            # A holiday range is flattened into ``values`` after the two
            # required arguments, so only the lower bound is fixed here.
            if len(values) < 2:
                return VALUE_ERROR
            try:
                start_part = _date_part(_to_number(values[0]))
                end_part = _date_part(_to_number(values[1]))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            if start_part is None or end_part is None:
                return NUM_ERROR
            # Bound the day-by-day loop: NETWORKDAYS(1, 10**9) would otherwise
            # stall the evaluating thread for hours on a read path.
            if abs(end_part - start_part) > MAX_NETWORKDAYS_SPAN_DAYS:
                return NUM_ERROR
            holidays = _holiday_serials(values[2:])
            # Walk the day serials by index rather than by adding a day to a
            # ``date``: the last day (9999-12-31) has no successor, so the
            # increment after it raised an OverflowError straight out of the
            # evaluator instead of answering the weekday count.
            count = 0
            for serial in range(start_part, end_part + 1):
                # Monday-Friday, excluding listed holidays. A holiday that
                # lands on a weekend is already excluded, so it cannot
                # subtract twice.
                if (
                    _DATE_EPOCH + datetime.timedelta(days=serial)
                ).isoweekday() <= 5 and serial not in holidays:
                    count += 1
            return float(count)
        elif name == "WORKDAY":
            # WORKDAY(start_date, days, [holidays])
            if len(values) < 2:
                return VALUE_ERROR
            try:
                start_part = _date_part(_to_number(values[0]))
                work_days = int(_to_number(values[1]))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            if start_part is None:
                return NUM_ERROR
            # A negative day count walks backwards, as every other
            # spreadsheet does; zero days is the start date itself.
            remaining = abs(work_days)
            step = 1 if work_days >= 0 else -1
            # Bound the walk: WORKDAY(1, 10**9) would otherwise loop
            # unbounded on a read path.
            if remaining > MAX_WORKDAY_OFFSET_DAYS:
                return NUM_ERROR
            holidays = _holiday_serials(values[2:])
            current = start_part
            while remaining > 0:
                current += step
                # The walk can leave the representable span (past 9999-12-31,
                # or before 0001-01-01); the result names no date, so it is
                # #NUM! rather than an OverflowError or an out-of-range
                # serial.
                if current < MIN_DATE_SERIAL or current > MAX_DATE_SERIAL:
                    return NUM_ERROR
                # Monday-Friday, excluding listed holidays.
                if (
                    _DATE_EPOCH + datetime.timedelta(days=current)
                ).isoweekday() <= 5 and current not in holidays:
                    remaining -= 1
            return float(current)
        return VALUE_ERROR

    # ── Advanced trigonometric functions ─────────────────────────────────────

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
            # An error written into the sum range propagates (an error cell
            # is read as 0 when its row matches, as below).
            sum_error = _computed_range_error(sum_range_node, sum_matrix)
            if sum_error is not None:
                return sum_error
            num_criteria = (len(args) - 1) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_node = args[1 + i * 2]
                cr_matrix = self._criteria_range(cr_node, sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                range_error = _computed_range_error(cr_node, cr_matrix)
                if range_error is not None:
                    return range_error
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
                cr_node = args[i * 2]
                cr_matrix = self._criteria_range(cr_node, sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                range_error = _computed_range_error(cr_node, cr_matrix)
                if range_error is not None:
                    return range_error
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
            average_error = _computed_range_error(average_range_node, average_matrix)
            if average_error is not None:
                return average_error
            num_criteria = (len(args) - 1) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_node = args[1 + i * 2]
                cr_matrix = self._criteria_range(cr_node, sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                range_error = _computed_range_error(cr_node, cr_matrix)
                if range_error is not None:
                    return range_error
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
    # The client engine implements 262 functions and this one a deliberately
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
        # An error operand propagates, as it does everywhere else: an error
        # *expression* or the error *search* the scan reaches is the answer,
        # where reading the error as just another value fell through to the
        # default. Only the operands the scan actually evaluates propagate,
        # which is the short-circuit rule the untaken branches already follow.
        if is_error(expression):
            return expression
        pairs = args[1:]
        for index in range(0, len(pairs) - 1, 2):
            search = self._eval(pairs[index], sheet, seen, depth)
            if is_error(search):
                return search
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
        """LARGE, SMALL, RANK, PERCENTILE, QUARTILE, the SUMX2* cross-sums,
        the scalar descriptives with extra parameters and the bivariate regressions.

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
            # A sum of squares past the doubles is #NUM!, not an infinity or
            # the NaN an ``inf - inf`` leaves; the client engine answers the
            # same.
            return _finite(total)
        if name == "TRIMMEAN":
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
            if not numbers or percent < 0 or percent >= 1:
                return NUM_ERROR
            numbers.sort()
            trim = math.floor(len(numbers) * percent / 2)
            trimmed = numbers[trim : len(numbers) - trim]
            return sum(trimmed) / len(trimmed)
        if name == "STANDARDIZE":
            if len(args) != 3:
                return VALUE_ERROR
            scalars = []
            for node in args:
                error, scalar_value = self._scalar_argument(node, sheet, seen, depth)
                if error is not None:
                    return error
                error, number = _read_number(scalar_value)
                if error is not None:
                    return error
                scalars.append(number)
            x, mean_value, deviation = scalars
            if deviation <= 0:
                return NUM_ERROR
            return (x - mean_value) / deviation
        if name == "PERCENTRANK":
            if len(args) < 2 or len(args) > 3:
                return VALUE_ERROR
            data, provided = self._expand_arg(args[0], sheet, seen, depth)
            error = _literal_error(data, provided)
            if error is not None:
                return error
            error, target_value = self._scalar_argument(args[1], sheet, seen, depth)
            if error is not None:
                return error
            error, target = _read_number(target_value)
            if error is not None:
                return error
            significance = 3.0
            if len(args) == 3:
                error, sig_value = self._scalar_argument(args[2], sheet, seen, depth)
                if error is not None:
                    return error
                error, significance = _read_number(sig_value)
                if error is not None:
                    return error
                # Excel rejects a significance below one outright.
                if significance < 1:
                    return NUM_ERROR
            numbers = _number_values(data)
            if not numbers:
                return NUM_ERROR
            if target < min(numbers) or target > max(numbers):
                return N_A_ERROR
            less_than = sum(1 for value in numbers if value < target)
            equal = sum(1 for value in numbers if value == target)
            rank = (less_than + 0.5 * equal) / len(numbers)
            # A double carries ~15 significant digits and the rounding factor
            # stops being representable past 1e308, so a larger significance
            # returns the unrounded rank. Raising the exponentiation used to
            # throw an OverflowError straight out of the evaluator.
            if not math.isfinite(significance) or math.floor(significance) > 308:
                return rank
            factor = 10.0 ** math.floor(significance)
            return round(rank * factor) / factor
        if name in ("CORREL", "RSQ", "SLOPE", "INTERCEPT", "FORECAST", "STEYX"):
            return self._bivariate(name, args, sheet, seen, depth)
        return VALUE_ERROR

    def _bivariate(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """CORREL, RSQ, SLOPE, INTERCEPT, FORECAST and STEYX.

        The paired-range regressions: two parallel numeric streams with the
        client engine's pairing rule (text and blanks are dropped per side, so
        mismatched lengths are #N/A) and its zero-variance guards.
        """
        if name == "FORECAST":
            if len(args) != 3:
                return VALUE_ERROR
            error, x_value = self._scalar_argument(args[0], sheet, seen, depth)
            if error is not None:
                return error
            error, x_target = _read_number(x_value)
            if error is not None:
                return error
            slope = self._bivariate("SLOPE", [args[1], args[2]], sheet, seen, depth)
            if is_error(slope):
                return slope
            known_y, y_ref = self._expand_arg(args[1], sheet, seen, depth)
            known_x, x_ref = self._expand_arg(args[2], sheet, seen, depth)
            y_numbers = _number_values(known_y)
            x_numbers = _number_values(known_x)
            mean_y = sum(y_numbers) / len(y_numbers)
            mean_x = sum(x_numbers) / len(x_numbers)
            return slope * x_target + mean_y - slope * mean_x
        if len(args) != 2:
            return VALUE_ERROR
        first, first_ref = self._expand_arg(args[0], sheet, seen, depth)
        second, second_ref = self._expand_arg(args[1], sheet, seen, depth)
        error = _literal_error(first, first_ref) or _literal_error(second, second_ref)
        if error is not None:
            return error
        # Excel drops text and blanks per side before pairing; mismatched
        # survivors are #N/A rather than a truncated pairing.
        if name in ("CORREL", "RSQ"):
            x_numbers = _number_values(first)
            y_numbers = _number_values(second)
        else:
            # SLOPE/INTERCEPT/STEYX read known_y first, known_x second.
            y_numbers = _number_values(first)
            x_numbers = _number_values(second)
        if not x_numbers or len(x_numbers) != len(y_numbers):
            return N_A_ERROR
        mean_x = sum(x_numbers) / len(x_numbers)
        mean_y = sum(y_numbers) / len(y_numbers)
        cross = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_numbers, y_numbers))
        if name in ("CORREL", "RSQ"):
            var_x = sum((x - mean_x) ** 2 for x in x_numbers)
            var_y = sum((y - mean_y) ** 2 for y in y_numbers)
            if var_x == 0 or var_y == 0:
                return DIV_ZERO
            correlation = cross / math.sqrt(var_x * var_y)
            return correlation * correlation if name == "RSQ" else correlation
        var_x = sum((x - mean_x) ** 2 for x in x_numbers)
        if var_x == 0:
            return DIV_ZERO
        slope = cross / var_x
        if name == "SLOPE":
            return slope
        intercept = mean_y - slope * mean_x
        if name == "INTERCEPT":
            return intercept
        # STEYX: the standard error of the predicted y for each x.
        if len(x_numbers) < 3:
            return DIV_ZERO
        residual = sum(
            (y - (slope * x + intercept)) ** 2 for x, y in zip(x_numbers, y_numbers)
        )
        return math.sqrt(residual / (len(x_numbers) - 2))

    def _adopted_statistical(
        self, name: str, values: List[Any], from_reference: List[bool]
    ) -> Any:
        """MEDIAN, MODE, STDEV, VAR, AVERAGEA, MAXA, MINA and the pooled descriptives.

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
        # The squared deviations are where the spread measures overflow: the
        # sum of the squares leaves the doubles long before the values do.
        # Python's ``** 2`` raises OverflowError there, where the client
        # engine's multiplication quietly answers Infinity, so multiply and
        # pass the result through ``_finite``; an overflowing double is not a
        # cell value and both engines answer #NUM!.
        if name in ("STDEV", "VAR"):
            if len(numbers) < 2:
                return DIV_ZERO
            mean = sum(numbers) / len(numbers)
            total = sum((value - mean) * (value - mean) for value in numbers)
            variance = total / (len(numbers) - 1)
            return _finite(math.sqrt(variance) if name == "STDEV" else variance)
        if name in ("STDEVP", "VARP"):
            if not numbers:
                return DIV_ZERO
            mean = sum(numbers) / len(numbers)
            total = sum((value - mean) * (value - mean) for value in numbers)
            variance = total / len(numbers)
            return _finite(math.sqrt(variance) if name == "STDEVP" else variance)
        if name == "AVEDEV":
            if not numbers:
                return DIV_ZERO
            mean = sum(numbers) / len(numbers)
            return _finite(sum(abs(value - mean) for value in numbers) / len(numbers))
        if name == "DEVSQ":
            if not numbers:
                return DIV_ZERO
            mean = sum(numbers) / len(numbers)
            total = sum((value - mean) * (value - mean) for value in numbers)
            return _finite(total)
        if name == "GEOMEAN":
            if not numbers or any(value < 0 for value in numbers):
                return NUM_ERROR
            if any(value == 0 for value in numbers):
                return 0.0
            log_sum = 0.0
            for value in numbers:
                log_sum += math.log(value)
            try:
                result = math.exp(log_sum / len(numbers))
            except OverflowError:
                return NUM_ERROR
            if not math.isfinite(result):
                return NUM_ERROR
            return result
        if name == "HARMEAN":
            if not numbers or any(value <= 0 for value in numbers):
                return NUM_ERROR
            reciprocal = sum(1.0 / value for value in numbers)
            return len(numbers) / reciprocal
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
            # A square past the doubles is #NUM!, not an infinity on the
            # sheet; the client engine answers the same.
            return _finite(total)
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
            # Every component is coerced before any of them is range-checked:
            # an argument that is not a number is #VALUE! no matter how a later
            # component looks. Checking each in turn reported #NUM! for
            # TIME(-1, "#", 0) where the client engine (and Excel) answer
            # #VALUE!.
            for value in values:
                error, number = _read_number(value)
                if error is not None:
                    return error
                parts.append(number)
            if any(number < 0 or number > MAX_TIME_COMPONENT for number in parts):
                return NUM_ERROR
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
            # The time readers name a time of day on a date, so a serial whose
            # date part is outside the covered span is #NUM!, exactly as it is
            # for YEAR/MONTH/DAY. Without the check a huge serial read a
            # fraction of zero and answered 0 here, while the client engine
            # (whose Date overflows to an Invalid Date) leaked a NaN into the
            # cell.
            if _date_part(number) is None:
                return NUM_ERROR
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
                discount = _pow_js(1 + rate, index + 1)
                # A rate of -1 makes the discount factor (1+rate)**n zero. That
                # is a division by zero rather than an overflow, so it is
                # #DIV/0! -- the answer the engine already gives the same zero
                # denominator in MIRR -- and not the #NUM! an infinite result
                # would earn. Dividing here used to raise ZeroDivisionError out
                # of the engine instead.
                if discount == 0:
                    return DIV_ZERO
                total += cashflow / discount
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
            # Excel needs a sign change for a rate to exist at all: at least one
            # negative cash flow (something to discount back) and at least one
            # positive one (something to compound forward). A lone value has
            # neither, and MIRR is #DIV/0! for that as well as for an all-one-sign
            # series; it used to read the sign by testing the two sums, which let
            # a single negative value fall through to the -1 a bare 0/0 makes.
            has_negative = any(cashflow < 0 for cashflow in cashflows)
            has_positive = any(cashflow > 0 for cashflow in cashflows)
            if last <= 0 or not (has_negative and has_positive):
                return DIV_ZERO
            pv_negative = 0.0
            fv_positive = 0.0
            for t, cashflow in enumerate(cashflows):
                if cashflow < 0:
                    discount = _pow_js(1 + finance_rate, t)
                    # A finance rate of -1 zeroes the discount factor of every
                    # period after the first, which is #DIV/0! and not the
                    # ZeroDivisionError this division used to raise.
                    if discount == 0:
                        return DIV_ZERO
                    pv_negative += cashflow / discount
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
                discount = _pow_js(1 + rate, (serial - start) / 365)
                # As in NPV, a rate of -1 zeroes the discount factor of every
                # period after the first, which is #DIV/0! and not #NUM!.
                if discount == 0:
                    return DIV_ZERO
                total += amount / discount
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
                if i > MAX_LOOP_ITERATIONS:
                    # A period long enough to walk this loop for hours is not a
                    # depreciation either engine can answer for; a formula is
                    # untrusted input, so it is #NUM! rather than a hang.
                    return NUM_ERROR
                if i == 1:
                    depreciation = cost * rate * month / 12
                elif i == life + 1:
                    depreciation = book_value * rate * (12 - month) / 12
                else:
                    depreciation = book_value * rate
                book_value -= depreciation
                if depreciation == 0 and i > life + 1:
                    # Past ``life + 1`` every remaining period takes the
                    # ``book_value * rate`` branch, and once that has
                    # underflowed to zero it stays zero: the book no longer
                    # moves, so the remaining periods cannot change the answer
                    # and the loop can stop instead of counting them out one at
                    # a time. The ``life + 1`` branch itself is excluded -- its
                    # ``12 - month`` factor makes it zero for a twelve-month
                    # year, and the periods after it are nonzero again.
                    break
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
                if successes > MAX_LOOP_ITERATIONS:
                    # One term per success; a count large enough to make that
                    # unbounded work is #NUM! rather than a hang.
                    return NUM_ERROR
                total = 0.0
                # ``_combin(trials, i)`` rebuilds its running product from
                # scratch on every call, which makes the sum quadratic. Walk
                # the same product once and round each step exactly as
                # ``_combin`` does, so every term stays bit-identical while
                # each iteration is O(1).
                coefficient = 1.0
                for i in range(successes + 1):
                    if i > 0:
                        coefficient = coefficient * (trials - (i - 1)) / i
                    rounded = (
                        float(math.floor(coefficient + 0.5))
                        if math.isfinite(coefficient)
                        else float("inf")
                    )
                    total += (
                        rounded
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
                if events > MAX_LOOP_ITERATIONS:
                    # One term per event; a count large enough to make that
                    # unbounded work is #NUM! rather than a hang.
                    return NUM_ERROR
                total = 0.0
                # ``_factorial`` rebuilds the product up to ``i`` on every
                # term, which makes the sum quadratic in ``i``. Carry the same
                # product across the loop instead: ``fact(i)`` is exactly that
                # running product, so every term stays bit-identical while
                # each iteration is O(1).
                factorial = 1.0
                for i in range(events + 1):
                    if i >= 2:
                        factorial *= i
                        if not math.isfinite(factorial):
                            factorial = float("inf")
                    total += math.exp(-mean) * _pow_js(mean, i) / factorial
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
                cdf = _betai(mid, alpha, beta)
                if not math.isfinite(cdf):
                    # An incomplete beta that left the reals cannot be inverted:
                    # the parameters are past what the double arithmetic can
                    # resolve, so answer #NUM! rather than converging on
                    # whatever the overflow left behind (the client engine
                    # makes the same check).
                    return NUM_ERROR
                if cdf < first:
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

    def _complex(self, name: str, values: List[Any]) -> Any:
        """Excel's complex-number family, mirroring the client engine.

        COMPLEX builds a value; an IM* function reads one (or two) complex
        arguments, keeps the ``i``/``j`` suffix of the first, and prints the
        result in Excel's complex-number text form.
        """
        if name == "COMPLEX":
            if not 1 <= len(values) <= 3:
                return VALUE_ERROR
            error, real_part = _read_number(values[0])
            if error is not None:
                return error
            imaginary_part = 0.0
            if len(values) >= 2:
                error, imaginary_part = _read_number(values[1])
                if error is not None:
                    return error
            suffix = "i"
            if len(values) == 3:
                suffix = _to_text(values[2]).lower()
                if suffix not in ("i", "j"):
                    return VALUE_ERROR
            return _format_complex(real_part, imaginary_part, suffix)

        if name in ("IMSUM", "IMPRODUCT"):
            if not values:
                return VALUE_ERROR
            real_total, imaginary_total = (0.0, 0.0) if name == "IMSUM" else (1.0, 0.0)
            suffix = "i"
            for index, value in enumerate(values):
                parsed = _parse_complex(value)
                if parsed is None:
                    return NUM_ERROR
                if index == 0:
                    suffix = parsed[2]
                if name == "IMSUM":
                    real_total += parsed[0]
                    imaginary_total += parsed[1]
                else:
                    real_total, imaginary_total = (
                        real_total * parsed[0] - imaginary_total * parsed[1],
                        real_total * parsed[1] + imaginary_total * parsed[0],
                    )
            return _format_complex(real_total, imaginary_total, suffix)

        if name in ("IMABS", "IMREAL", "IMAGINARY", "IMCONJUGATE", "IMARGUMENT"):
            if len(values) != 1:
                return VALUE_ERROR
            parsed = _parse_complex(values[0])
            if parsed is None:
                return NUM_ERROR
            if name == "IMABS":
                return math.hypot(parsed[0], parsed[1])
            if name == "IMREAL":
                return parsed[0]
            if name == "IMAGINARY":
                return parsed[1]
            if name == "IMCONJUGATE":
                return _format_complex(parsed[0], -parsed[1], parsed[2])
            if parsed[0] == 0 and parsed[1] == 0:
                return DIV_ZERO
            return math.atan2(parsed[1], parsed[0])

        if name in ("IMSUB", "IMDIV", "IMMOD"):
            if len(values) != 2:
                return VALUE_ERROR
            first = _parse_complex(values[0])
            second = _parse_complex(values[1])
            if first is None or second is None:
                return NUM_ERROR
            if name == "IMSUB":
                return _format_complex(
                    first[0] - second[0], first[1] - second[1], first[2]
                )
            quotient = _complex_divide(first, second)
            if quotient is None:
                return DIV_ZERO
            if name == "IMDIV":
                return _format_complex(quotient[0], quotient[1], first[2])
            if not (math.isfinite(quotient[0]) and math.isfinite(quotient[1])):
                # ``math.trunc`` of an infinity raises rather than answering.
                return NUM_ERROR
            quotient_real = math.trunc(quotient[0])
            quotient_imaginary = math.trunc(quotient[1])
            return _format_complex(
                first[0] - (quotient_real * second[0] - quotient_imaginary * second[1]),
                first[1] - (quotient_real * second[1] + quotient_imaginary * second[0]),
                first[2],
            )

        if len(values) != 1:
            return VALUE_ERROR
        parsed = _parse_complex(values[0])
        if parsed is None:
            return NUM_ERROR
        if name == "IMEXP":
            magnitude = _overflowing(math.exp, parsed[0])
            result = (magnitude * math.cos(parsed[1]), magnitude * math.sin(parsed[1]))
        elif name == "IMLOG":
            magnitude = math.hypot(parsed[0], parsed[1])
            if magnitude == 0:
                return NUM_ERROR
            result = (math.log(magnitude), math.atan2(parsed[1], parsed[0]))
        elif name == "IMSQRT":
            radius = math.sqrt(math.hypot(parsed[0], parsed[1]))
            theta = math.atan2(parsed[1], parsed[0]) / 2
            result = (radius * math.cos(theta), radius * math.sin(theta))
        elif name in ("IMCOS", "IMSIN", "IMTAN", "IMCSC", "IMSEC", "IMCOT"):
            sine = _complex_sin(parsed)
            cosine = _complex_cos(parsed)
            result = {
                "IMCOS": cosine,
                "IMSIN": sine,
                "IMTAN": _complex_divide(sine, cosine),
                "IMCSC": _complex_divide((1.0, 0.0), sine),
                "IMSEC": _complex_divide((1.0, 0.0), cosine),
                "IMCOT": _complex_divide(cosine, sine),
            }[name]
            if result is None:
                return DIV_ZERO
        elif name in ("IMCOSH", "IMSINH", "IMTANH", "IMCSCH", "IMSECH", "IMCOTH"):
            hyperbolic_sine = _complex_sinh(parsed)
            hyperbolic_cosine = _complex_cosh(parsed)
            result = {
                "IMCOSH": hyperbolic_cosine,
                "IMSINH": hyperbolic_sine,
                "IMTANH": _complex_divide(hyperbolic_sine, hyperbolic_cosine),
                "IMCSCH": _complex_divide((1.0, 0.0), hyperbolic_sine),
                "IMSECH": _complex_divide((1.0, 0.0), hyperbolic_cosine),
                "IMCOTH": _complex_divide(hyperbolic_cosine, hyperbolic_sine),
            }[name]
            if result is None:
                return DIV_ZERO
        else:
            return NAME_ERROR
        return _format_complex(result[0], result[1], parsed[2])

    def _engineering(self, name: str, values: List[Any]) -> Any:
        """The engineering functions: base conversions, bitwise operations,
        DELTA/GESTEP, ERF/ERFC, the Bessel functions and CONVERT.

        Every parameter is a scalar, so these read the pooled ``values`` the
        dispatcher builds; an error operand has already propagated on the way
        in. The coercion mirrors the client engine's: a conversion input is
        stringified (a blank is ``"null"``, hence ``#NUM!``), every other
        parameter goes through the numeric coercion, and ``places`` counts
        characters the way ``padStart`` does.
        """
        if name in ("BIN2DEC", "BIN2HEX", "BIN2OCT"):
            if not 1 <= len(values) <= 2:
                return VALUE_ERROR
            if name == "BIN2DEC":
                raw = _js_string(values[0]).strip()
                if len(raw) > 10:
                    return NUM_ERROR
                parsed = _parse_base(values[0], 2)
                if parsed is None:
                    return NUM_ERROR
                if len(raw) == 10 and raw[0] == "1":
                    parsed -= 1024
                # The second slot is accepted and ignored, as in the client
                # engine -- it is never coerced, so even text reads past it.
                return parsed
            # Places is coerced before the input is validated, the way the
            # client engine orders it: BIN2OCT(18, "abc") is #VALUE! (bad
            # places), not #NUM! (bad input).
            places = None
            if len(values) == 2:
                error, places = _read_number(values[1])
                if error is not None:
                    return error
            raw = _js_string(values[0]).strip()
            if len(raw) > 10:
                return NUM_ERROR
            parsed = _parse_base(values[0], 2)
            if parsed is None:
                return NUM_ERROR
            if len(raw) == 10 and raw[0] == "1":
                parsed -= 1024
            return _to_base(float(parsed), 16 if name == "BIN2HEX" else 8, places)
        if name in ("DEC2BIN", "DEC2HEX", "DEC2OCT"):
            if not 1 <= len(values) <= 2:
                return VALUE_ERROR
            error, number = _read_number(values[0])
            if error is not None:
                return error
            places = None
            if len(values) == 2:
                error, places = _read_number(values[1])
                if error is not None:
                    return error
            if name == "DEC2BIN":
                if number < -512 or number > 511:
                    return NUM_ERROR
                return _to_base(number, 2, places)
            if name == "DEC2HEX":
                if number < -549755813888 or number > 549755813887:
                    return NUM_ERROR
                return _to_base(number, 16, places)
            if number < -536870912 or number > 536870911:
                return NUM_ERROR
            return _to_base(number, 8, places)
        if name in ("HEX2BIN", "HEX2DEC", "HEX2OCT"):
            if not 1 <= len(values) <= 2:
                return VALUE_ERROR
            if name == "HEX2DEC":
                raw = _js_string(values[0]).strip()
                if len(raw) > 10:
                    return NUM_ERROR
                parsed = _parse_base(values[0], 16)
                if parsed is None:
                    return NUM_ERROR
                if len(raw) == 10 and raw[0] in ("F", "f"):
                    parsed -= 16**10
                # The decimal form takes no padding: the extra argument is
                # accepted and never coerced, so even text reads past it.
                return parsed
            # Places before input validation, as in the BIN2* branch above.
            places = None
            if len(values) == 2:
                error, places = _read_number(values[1])
                if error is not None:
                    return error
            raw = _js_string(values[0]).strip()
            if len(raw) > 10:
                return NUM_ERROR
            parsed = _parse_base(values[0], 16)
            if parsed is None:
                return NUM_ERROR
            if name != "HEX2BIN" and len(raw) == 10 and raw[0] in ("F", "f"):
                # A 10-digit hex starting with F is negative -- except as a
                # HEX2BIN input, which the client reads unsigned and rejects
                # past 511 instead.
                parsed -= 16**10
            if name == "HEX2BIN":
                if parsed > 511:
                    return NUM_ERROR
                return _to_base(float(parsed), 2, places)
            if parsed > 536870911:
                return NUM_ERROR
            return _to_base(float(parsed), 8, places)
        if name in ("OCT2BIN", "OCT2DEC", "OCT2HEX"):
            if not 1 <= len(values) <= 2:
                return VALUE_ERROR
            if name == "OCT2DEC":
                raw = _js_string(values[0]).strip()
                if len(raw) > 10:
                    return NUM_ERROR
                parsed = _parse_base(values[0], 8)
                if parsed is None:
                    return NUM_ERROR
                if len(raw) == 10 and raw[0] == "7":
                    parsed -= 8**10
                # As with HEX2DEC, the extra argument is accepted and
                # never coerced.
                return parsed
            # Places before input validation, as in the BIN2* branch above.
            places = None
            if len(values) == 2:
                error, places = _read_number(values[1])
                if error is not None:
                    return error
            raw = _js_string(values[0]).strip()
            if len(raw) > 10:
                return NUM_ERROR
            parsed = _parse_base(values[0], 8)
            if parsed is None:
                return NUM_ERROR
            if name != "OCT2BIN" and len(raw) == 10 and raw[0] == "7":
                # As with hex, the 10-digit negative form does not apply to
                # the OCT2BIN input, which is read unsigned instead.
                parsed -= 8**10
            if name == "OCT2BIN":
                if parsed > 511:
                    return NUM_ERROR
                return _to_base(float(parsed), 2, places)
            if parsed > 549755813887:
                return NUM_ERROR
            return _to_base(float(parsed), 16, places)
        if name in ("BITAND", "BITOR", "BITXOR"):
            if len(values) != 2:
                return VALUE_ERROR
            error, first = _read_number(values[0])
            if error is not None:
                return error
            error, second = _read_number(values[1])
            if error is not None:
                return error
            if first < 0 or second < 0 or first >= 2**48 or second >= 2**48:
                return NUM_ERROR
            left, right = _to_int32(first), _to_int32(second)
            if name == "BITAND":
                return left & right
            if name == "BITOR":
                return left | right
            return left ^ right
        if name in ("BITLSHIFT", "BITRSHIFT"):
            if len(values) != 2:
                return VALUE_ERROR
            error, number = _read_number(values[0])
            if error is not None:
                return error
            error, shift = _read_number(values[1])
            if error is not None:
                return error
            if number < 0 or number >= 2**48 or shift < -53 or shift > 53:
                return NUM_ERROR
            if name == "BITLSHIFT":
                if shift >= 0:
                    return number * 2.0**shift
                return math.floor(number / 2.0**-shift)
            if shift >= 0:
                return math.floor(number / 2.0**shift)
            return number * 2.0**-shift
        if name == "DELTA":
            if not 1 <= len(values) <= 2:
                return VALUE_ERROR
            error, first = _read_number(values[0])
            if error is not None:
                return error
            second = 0.0
            if len(values) == 2:
                error, second = _read_number(values[1])
                if error is not None:
                    return error
            return 1 if first == second else 0
        if name == "GESTEP":
            if not 1 <= len(values) <= 2:
                return VALUE_ERROR
            error, number = _read_number(values[0])
            if error is not None:
                return error
            step = 0.0
            if len(values) == 2:
                error, step = _read_number(values[1])
                if error is not None:
                    return error
            return 1 if number >= step else 0
        if name == "ERF":
            if not 1 <= len(values) <= 2:
                return VALUE_ERROR
            error, lower = _read_number(values[0])
            if error is not None:
                return error
            if len(values) == 1:
                return _erf_single(lower)
            error, upper = _read_number(values[1])
            if error is not None:
                return error
            return _erf_single(upper) - _erf_single(lower)
        if name == "ERFC":
            if len(values) != 1:
                return VALUE_ERROR
            error, number = _read_number(values[0])
            if error is not None:
                return error
            return 1.0 - _erf_single(number)
        if name == "BESSELJ":
            if len(values) != 2:
                return VALUE_ERROR
            error, x = _read_number(values[0])
            if error is not None:
                return error
            error, order = _read_number(values[1])
            if error is not None:
                return error
            n = math.floor(order)
            if n < 0:
                return NUM_ERROR
            try:
                return _finite(_bessel_j(x, n))
            except OverflowError:
                # A term of the series overflowed the doubles: the argument is
                # past the range the 40-term series can hold, and an
                # overflowing double is not a cell value.
                return NUM_ERROR
        if name == "BESSELY":
            if len(values) != 2:
                return VALUE_ERROR
            error, x = _read_number(values[0])
            if error is not None:
                return error
            error, order = _read_number(values[1])
            if error is not None:
                return error
            n = math.floor(order)
            if x <= 0 or n < 0:
                return NUM_ERROR
            try:
                y0 = _bessel_y0_series(x)
                # The client reads J0 and J1 before it looks at the order -- the
                # Y0 series is built on J0 and its Y1 estimate on J1 -- so a
                # J-series term that overflows the doubles is #NUM! even for
                # BESSELY(x, 0). Mirror that, or a large x (where the J1 series
                # overflows while the Y0 series does not) answered a finite but
                # garbage value here while the client answered #NUM!, which the
                # README pins as the shared answer past the series' range.
                j0 = _bessel_j(x, 0)
                j1 = _bessel_j(x, 1)
                if n == 0:
                    return _finite(y0)
                if abs(j0) > 1e-14:
                    # The Wronskian identity J1*Y0 - J0*Y1 = 2/(pi*x), solved
                    # for Y1 -- exact to the precision of the Y0 series.
                    y1 = (j1 * y0 - 2 / (math.pi * x)) / j0
                else:
                    # J0(x) is near a zero of J0: Y1 = -Y0' by central
                    # differences of the series.
                    eps = 1e-6 * max(1, x)
                    y1 = -(_bessel_y0_series(x + eps) - _bessel_y0_series(x - eps)) / (
                        2 * eps
                    )
                if n == 1:
                    return _finite(y1)
                y_previous, y_current = y0, y1
                for index in range(1, n):
                    y_previous, y_current = (
                        y_current,
                        (2 * index / x) * y_current - y_previous,
                    )
                    if not math.isfinite(y_current):
                        # The recurrence leaves the doubles long before it
                        # reaches a huge order; stopping here keeps the loop
                        # from spinning to the end on values no cell can hold.
                        return NUM_ERROR
                return _finite(y_current)
            except OverflowError:
                # As with BESSELJ above, a term of the series overflowed the
                # doubles, so the argument is past what the series can hold.
                return NUM_ERROR
        if name == "CONVERT":
            if len(values) != 3:
                return VALUE_ERROR
            error, number = _read_number(values[0])
            if error is not None:
                return error
            source = _CONVERT_UNITS.get(_js_string(values[1]).strip().upper())
            target = _CONVERT_UNITS.get(_js_string(values[2]).strip().upper())
            if source is None or target is None:
                return N_A_ERROR
            if source[0] == "temperature" and target[0] == "temperature":
                if _js_string(values[1]).strip().upper() in ("C", "CELSIUS", "CEL"):
                    celsius = number
                elif _js_string(values[1]).strip().upper() in (
                    "F",
                    "FAHRENHEIT",
                    "FAH",
                ):
                    celsius = (number - 32) * 5 / 9
                else:
                    celsius = number - 273.15
                if _js_string(values[2]).strip().upper() in ("C", "CELSIUS", "CEL"):
                    return celsius
                if _js_string(values[2]).strip().upper() in (
                    "F",
                    "FAHRENHEIT",
                    "FAH",
                ):
                    return celsius * 9 / 5 + 32
                return celsius + 273.15
            if source[0] != target[0]:
                return N_A_ERROR
            if source[1] is None or target[1] is None:
                return N_A_ERROR
            return number * source[1] / target[1]
        return NAME_ERROR
