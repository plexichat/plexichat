"""Criteria matching for SUMIF/COUNTIF/AVERAGEIF."""

from typing import Any, List, Optional

from .._formula_shared import (
    DIV_ZERO,
    VALUE_ERROR,
    _computed_range_error,
    _criteria_operator,
    _fold_case,
    _is_pattern,
    _numeric,
    _to_number,
    _to_number_or_zero,
    _to_text,
    _wildcard_match,
    is_error,
)
from .base import FormulaEvaluatorCoreBase


class CriteriaFunctionsMixin(FormulaEvaluatorCoreBase):
    """Excel-style criteria parsing and comparison."""

    def _criteria(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """SUMIF / COUNTIF / AVERAGEIF with operator-prefixed criteria."""
        if len(args) < 2 or len(args) > 3:
            return VALUE_ERROR
        matrix = self._criteria_range(args[0], sheet, seen, depth)
        if not matrix:
            return DIV_ZERO if name == "AVERAGEIF" else 0.0
        # An error written into the range propagates; an error cell only
        # fails to match (see _computed_range_error).
        range_error = _computed_range_error(args[0], matrix)
        if range_error is not None:
            return range_error
        first = args[1]
        if first[0] == "str":
            criteria: Any = first[1]
        elif first[0] == "num":
            criteria = first[1]
        else:
            # A criteria argument is a single value, so a matrix -- a range or
            # a nested call -- follows the pooled rule (the client's
            # scalarize): SUMIF(1, FREQUENCY(A1:A5, A1:A5)) is #VALUE! and
            # SUMIF(1, OFFSET(A1, 0, 0, 5, 1)) propagates the error cell.
            criteria = self._scalar_or(first, sheet, seen, depth)
        if is_error(criteria) and first[0] != "ref":
            # An error *written* as the criteria propagates straight away --
            # SUMIF(1, 1/0, NA()) is #DIV/0! and SUMIF(1, NA(), 1/0) is #N/A.
            return criteria
        if name == "COUNTIF" and len(args) != 2:
            # COUNTIF takes exactly two arguments -- SUMIF and AVERAGEIF take
            # an optional sum range, but a third argument to COUNTIF is
            # #VALUE!.
            return VALUE_ERROR
        sum_matrix = matrix
        if len(args) == 3:
            sum_matrix = self._criteria_range(args[2], sheet, seen, depth)
            sum_error = _computed_range_error(args[2], sum_matrix)
            if sum_error is not None:
                return sum_error
        if is_error(criteria):
            # A criteria read out of a *cell* propagates too, but only after
            # the summed range's own error: SUMIF(1, A5, NA()) is the summed
            # range's #N/A while SUMIF(1, A5, 1) is the criteria cell's #DIV/0!.
            return criteria
        # Both ranges are read flat, position by position -- the client
        # engine's ``getRangeValues`` flattens a range row-major, so the
        # criteria range and the sum range align by flat index even when their
        # shapes differ (a function-returned matrix, or a row against a
        # column). Aligning the two 2-D matrices by (row, column) instead
        # compared only the first element when one side was a single row.
        flat = [cell for row in matrix for cell in row]
        flat_sum = [cell for row in sum_matrix for cell in row]
        total = 0.0
        count = 0
        for index, cell in enumerate(flat):
            if not self._criteria_test(criteria, cell):
                continue
            value = flat_sum[index] if index < len(flat_sum) else None
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
            if _is_pattern(criteria):
                # A bare pattern (``COUNTIF(range, "tw*")``) is Excel's ``=``
                # with wildcards, escapes honoured: only text cells can match
                # it, so ``COUNTIF(range, "tw~*")`` finds the text ``tw*``.
                return isinstance(value, str) and _wildcard_match(criteria, value)
            return _fold_case(_to_text(value)) == _fold_case(criteria)
        if isinstance(value, bool) or isinstance(criteria, bool):
            # Criteria rank types the way every other comparison here does, so
            # a logical criterion matches only a logical cell of the same
            # value: COUNTIF(range, 1) does not count TRUE.
            return (
                isinstance(value, bool)
                and isinstance(criteria, bool)
                and value == criteria
            )
        if isinstance(value, str):
            return value == criteria
        if value is None:
            # A blank is not the number it coerces to in arithmetic, so
            # COUNTIF(range, 0) counts zero cells rather than every empty one.
            return False
        try:
            return _to_number(value) == _to_number(criteria)
        except ValueError:
            return False
