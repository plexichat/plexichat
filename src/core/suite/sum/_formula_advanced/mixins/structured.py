"""Formula evaluator mixin: structured and statistical functions."""

import math
from typing import Any, List, Optional
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    DIV_ZERO,
    NUM_ERROR,
    N_A_ERROR,
    VALUE_ERROR,
    is_error,
)
from ..helpers.numbers import _finite, _literal_error, _number_values, _read_number


class StructuredMixin(AdvancedEvaluatorBase):
    def _scalar_argument(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> tuple:
        """Evaluate one argument that must be a single value.

        Returns ``(error, value)``. A range read where one value belongs
        follows the pooled rule the client engine's ``scalarize`` applies: an
        error cell propagates, a one-cell range is that cell and anything else
        is #VALUE!. Checking the cell count first answered #VALUE! for a range
        that held an error, where the client engine propagated it --
        ``LARGE(A1:A5, A1:A5)`` was #VALUE! here and #DIV/0! there.
        """
        values, _ = self._expand_arg(node, sheet, seen, depth)
        for value in values:
            if is_error(value):
                return value, None
        if len(values) != 1:
            return VALUE_ERROR, None
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
            left, _ = self._expand_arg(args[0], sheet, seen, depth)
            right, _ = self._expand_arg(args[1], sheet, seen, depth)
            # An argument that is a single error value -- written into the
            # formula or a one-cell reference that holds one -- propagates
            # before the pair is read, the way the client engine's eager
            # argument evaluation throws it before the function body. That
            # error therefore beats a length mismatch or a text coercion in
            # the other argument. An error embedded in a *multi-cell* range is
            # not pre-propagated: the element read below reaches it in pairing
            # order, so a text cell before it still answers #VALUE!.
            for values in (left, right):
                if len(values) == 1 and is_error(values[0]):
                    return values[0]
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
            # Round half away from zero, the way the client engine's
            # ``Math.round`` does. Python's ``round`` is half-to-even, so a
            # rank landing exactly on a half -- PERCENTRANK(A1:C1, 1, 1), whose
            # rank is 0.25 -- answered 0.2 here where the client answers 0.3.
            # The rank is never negative, so ``floor(x + 0.5)`` is exact.
            return math.floor(rank * factor + 0.5) / factor
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
