"""Date/time parsing and serial conversion helpers."""

import datetime
import math
from typing import Any, List, Optional
from ..._formula_shared import (
    MAX_DATE_SERIAL,
    MIN_DATE_SERIAL,
    _DATE_EPOCH,
    _to_number,
    is_error,
)


def _holiday_serials(values: List[Any]) -> set:
    """Collect the day-serials of an optional ``holidays`` argument.

    ``_expand_arg`` flattens a range into ``values``, so blanks and non-date
    cells (a stray label beside the real dates) can sit alongside them. Excel
    ignores those rather than failing the whole formula, so a value that does
    not coerce to a number is skipped instead of poisoning the result. Each
    holiday names the day its rounded serial's integer part (truncated toward
    zero, matching the client engine), so a serial a hair below a boundary
    counts as the boundary's day.
    """
    serials = set()
    for value in values:
        if value is None or is_error(value):
            continue
        try:
            serials.add(math.trunc(_round_serial(_to_number(value))))
        except (TypeError, ValueError):
            continue
    return serials


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


def _round_serial(serial: float) -> float:
    """A serial rounded to the 15 significant decimal digits Excel stores.

    Excel keeps only 15 significant digits, so a serial carrying more is stored
    rounded: ``45291.99999999999`` is stored as ``45292`` and names 2024-01-01,
    and ``0.9999999999999999`` is stored as ``1`` (midnight). Every date *and*
    time part is read from the rounded serial, so both engines answer the
    boundary value's date and time rather than the raw fraction's.
    """
    return float(f"{serial:.15g}")


def _date_part(serial: float) -> Optional[int]:
    """The date a serial names, or ``None`` when it names none.

    The serial's integer part (truncated toward zero, matching the client
    engine's ``Math.trunc``) is the date and its fraction is a time of day, so
    the range is checked on the part, not on the raw serial: ``2958465.5``
    names 9999-12-31 and is in range. The serial is rounded to Excel's 15
    significant digits first, so a value a hair below a day boundary reads the
    boundary's date. A serial whose date part falls outside the covered span is
    ``None``, which the caller reports as #NUM!.
    """
    if not math.isfinite(serial):
        return None
    part = math.trunc(_round_serial(serial))
    if part < MIN_DATE_SERIAL or part > MAX_DATE_SERIAL:
        return None
    return part


def _time_of_day(serial: float) -> int:
    """The seconds past midnight the serial names.

    Read from the whole milliseconds the serial carries: the time of day is
    ``trunc(serial * 86400000)`` reduced to one day. A serial naming 41.8
    seconds reads 41, and truncating toward the second (rather than rounding)
    is what the client -- and Excel -- answer.

    Excel keeps only 15 significant decimal digits, so the serial is rounded
    there first: 0.9999999999999999 is stored as 1 and reads midnight, not
    23:59:59. Without the rounding the two engines disagreed in *both*
    directions at a boundary -- the server answered 23:59:59 here while the
    client's Date (built on the ~2e12 ms epoch, which rounded the extra
    fraction up) answered 00:00:00, and the client rounded a computed serial a
    hair below a second *up* where the server answered the lower second.
    """
    total_ms = math.trunc(_round_serial(serial) * 86400000.0)
    return (total_ms % 86400000) // 1000 % 86400


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
