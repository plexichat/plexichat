"""Formula evaluator mixin: adopted statistical functions."""

import datetime
import math
from typing import Any, List
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    DIV_ZERO,
    NUM_ERROR,
    N_A_ERROR,
    REF_ERROR,
    VALUE_ERROR,
    _DATE_EPOCH,
    _numeric,
    _to_text,
    is_error,
)
from ..helpers.dates import _date_part, _parse_date_text, _time_of_day
from ..helpers.numbers import (
    MAX_EXACT_INTEGER,
    MAX_TIME_COMPONENT,
    _finite,
    _literal_error,
    _number_or_zero,
    _number_values,
    _read_number,
)


class AdoptedMixin(AdvancedEvaluatorBase):
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
            # Excel's ATAN2(x_num, y_num) is the angle of the point
            # (x_num, y_num): mathematically atan2(y_num, x_num). Passing the
            # arguments through in order returned the reflected angle, so
            # ATAN2(1, 0) answered pi/2 instead of 0.
            return math.atan2(y, x)
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
