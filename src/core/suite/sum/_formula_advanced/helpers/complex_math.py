"""Complex-number parsing, formatting and arithmetic helpers."""

import math
from typing import Any
from ..._formula_shared import (
    NUMERIC_TEXT_RE,
    NUM_ERROR,
    _to_text,
)


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
