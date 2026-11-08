"""Base conversion, Bessel and unit-table helpers."""

import math
from typing import Any, Optional
from ..._formula_shared import (
    NUM_ERROR,
    VALUE_ERROR,
    _js_number_text,
)
from .numbers import _js_string
from .statistics import _fact_float

# A text cell can hold at most Excel's 32767 characters. Bounding the base
# parser before it builds a multi-megabyte Python integer keeps a hostile cell
# value from turning BIN2DEC/HEX2DEC into an unbounded big-int computation.
MAX_BASE_TEXT_LENGTH = 32_767

# BESSELY continues its recurrence one step per order. Excel cannot represent
# useful results past a few thousand orders (the terms underflow), so cap the
# loop instead of letting BESSELY(1, 10**9) spin a billion iterations.
MAX_BESSEL_ORDER = 10_000

# The base conversions (BIN2DEC .. OCT2HEX). Their input is scalarized before
# the places are coerced, so the family shares one early error check in
# ``_engineering``.
_BASE_CONVERSION_FUNCTIONS = frozenset(
    {
        "BIN2DEC",
        "BIN2HEX",
        "BIN2OCT",
        "DEC2BIN",
        "DEC2HEX",
        "DEC2OCT",
        "HEX2BIN",
        "HEX2DEC",
        "HEX2OCT",
        "OCT2BIN",
        "OCT2DEC",
        "OCT2HEX",
    }
)


def _parse_base(value: Any, base: int) -> Optional[int]:
    """Read base-``base`` text the way the conversions do, or ``None``.

    ``None`` means the text is not a number in that base, which the caller
    turns into ``#NUM!``. The input is trimmed, upper-cased and -- for the
    hexadecimal conversions only -- stripped of a single ``0X`` prefix; an
    empty remainder reads as 0. ``X`` is not a binary or octal digit, so
    stripping the prefix there turned ``BIN2DEC("0X10")`` into 2 instead of
    ``#NUM!``.
    """
    text = _js_string(value).strip().upper()
    if len(text) > MAX_BASE_TEXT_LENGTH:
        return None
    if base == 16 and text.startswith("0X"):
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
