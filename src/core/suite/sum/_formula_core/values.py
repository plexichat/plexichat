"""Value-returning function families and scalar-slot helpers."""

import math
import re
from typing import Any, List, Optional, Tuple

from .._formula_shared import (
    DIV_ZERO,
    ERROR_CODES,
    N_A_ERROR,
    NUM_ERROR,
    VALUE_ERROR,
    _coerce_number_text,
    _numeric,
    _to_number,
    _to_text,
    _utf16_len,
    is_error,
)
from .base import FormulaEvaluatorCoreBase


class ValueFunctionsMixin(FormulaEvaluatorCoreBase):
    """Aggregates, single-value functions, and argument coercion."""

    def _truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if _numeric(value):
            return value != 0
        if isinstance(value, str) and value not in ERROR_CODES:
            # Empty text is falsy, as in Excel (IF("", 1, 2) takes the else
            # branch) and in the client engine.
            return len(value) > 0
        return bool(value)

    def _aggregate(
        self, name: str, values: List[Any], from_reference: List[bool]
    ) -> Any:
        """SUM/AVERAGE/COUNT/COUNTA/MIN/MAX/PRODUCT over flattened arguments.

        ``from_reference`` runs parallel to *values* and marks the entries that
        came out of a reference, because Excel treats the two differently: a
        text *argument* that reads as a number is coerced (``SUM("2")`` is 2)
        while a value read from a reference is not (``SUM(A1)`` is 0 when A1
        holds the text "2").
        """
        if name == "COUNTA":
            return sum(1 for value in values if value is not None and value != "")
        numbers: List[Any] = []
        for value, ranged in zip(values, from_reference):
            if _numeric(value):
                numbers.append(value)
            elif not ranged:
                coerced = _coerce_number_text(value)
                if coerced is not None:
                    numbers.append(coerced)
        if name == "COUNT":
            # Excel's COUNT counts numbers only: an error value or non-numeric
            # text in the arguments is ignored rather than counted, and a text
            # argument counts only when it reads as a number.
            return len(numbers)
        # SUM/AVERAGE/MIN/MAX/PRODUCT propagate an error operand. Filtering it
        # out with the non-numeric cells silently returned a plausible but
        # wrong total (``SUM(1, 1/0)`` was 1, not ``#DIV/0!``). An error
        # *written into* the formula propagates ahead of an error cell in a
        # referenced range, in argument order, so ``SUM(A1:A5, NA())`` is #N/A
        # while ``SUM(A1:A5)`` is the range's #DIV/0!.
        for value, ranged in zip(values, from_reference):
            if is_error(value) and not ranged:
                return value
        for value in values:
            if is_error(value):
                return value
        if name == "SUM":
            return float(sum(numbers))
        if name == "PRODUCT":
            # PRODUCT of no numeric values is 0, not the mathematical empty
            # product of 1: Excel returns 0 for PRODUCT() and for a range that
            # holds no numbers, and the client engine agrees.
            if not numbers:
                return 0.0
            product = 1.0
            for value in numbers:
                product *= value
            return product
        if not numbers:
            if name == "MIN" or name == "MAX":
                return 0
            return DIV_ZERO if name == "AVERAGE" else 0
        if name == "AVERAGE":
            return float(sum(numbers)) / len(numbers)
        if name == "MIN":
            return min(numbers)
        if name == "MAX":
            return max(numbers)
        return VALUE_ERROR

    def _single(self, name: str, values: List[Any]) -> Any:
        # Fixed arity: a stray argument is #VALUE!, the way the client
        # engine answers it. Reading only the first value silently agreed
        # on every valid call while disagreeing on ABS(1, 2) and friends.
        if len(values) != 1:
            return VALUE_ERROR
        value = values[0]
        # The IS* family answers a question *about* its operand, so an error
        # value is ordinary input: ISNUMBER(#DIV/0!) is FALSE rather than the
        # error itself. Every function below propagates it instead.
        if name == "ISBLANK":
            # TRUE only for a genuinely empty cell. A cell holding the empty
            # string (the result of something like =IF(1=2,"","x")) contains
            # text, so ISBLANK("") is FALSE as in Excel.
            return value is None
        if name == "ISNUMBER":
            return _numeric(value)
        if name == "ISTEXT":
            return isinstance(value, str) and value not in ERROR_CODES
        if is_error(value):
            return value
        if name == "ABS":
            return abs(_to_number(value))
        if name == "SQRT":
            number = _to_number(value)
            if number < 0:
                return NUM_ERROR
            return math.sqrt(number)
        if name == "INT":
            return float(math.floor(_to_number(value)))
        if name == "LEN":
            # Excel counts UTF-16 code units, so an astral character (an emoji)
            # is two: LEN("😀") is 2. Python's len counts code points, which
            # answered 1 here while the client engine -- a UTF-16 string by
            # definition -- answered 2.
            return float(_utf16_len(_to_text(value)))
        if name == "UPPER":
            return _to_text(value).upper()
        if name == "LOWER":
            return _to_text(value).lower()
        if name == "TRIM":
            # Excel deletes leading/trailing spaces and collapses every
            # internal run down to one space. ``str.strip()`` only handled the
            # ends, so "a   b" came back unchanged -- and it also stripped
            # tabs, which Excel's TRIM leaves alone because it only knows
            # about spaces.
            return re.sub(r" +", " ", _to_text(value)).strip(" ")
        return VALUE_ERROR

    def _information(self, name: str, values: List[Any]) -> Any:
        """ISERROR, ISNA, NA and TYPE.

        These inspect an error operand instead of propagating it: the IS*
        family answers a question *about* the value, so ``ISERROR(1/0)`` is
        TRUE. That is what makes ``IF(ISERROR(x), ...)`` usable as a guard.
        """
        if name == "NA":
            # NA() takes no arguments and *is* the #N/A error value.
            return VALUE_ERROR if values else N_A_ERROR
        if len(values) != 1:
            return VALUE_ERROR
        value = values[0]
        if name == "ISERROR":
            return is_error(value)
        if name == "ISNA":
            return value == N_A_ERROR
        if name == "TYPE":
            # Excel's type codes: 1 number, 2 text, 4 logical, 16 error.
            # ``is_error`` has to be tested before the text branch because an
            # error value is a string.
            if isinstance(value, bool):
                return 4
            if _numeric(value):
                return 1
            if is_error(value):
                return 16
            if isinstance(value, str):
                return 2
            return 1
        return VALUE_ERROR

    def _scalar_slot(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> Tuple[Optional[str], Any]:
        """Read one scalar slot through the pooled range rule.

        An error cell in the slot propagates, a one-cell slot is that cell
        and anything else is #VALUE! -- the client engine's ``scalarize``.
        Returns ``(error, value)``.
        """
        values, _ = self._expand_arg(node, sheet, seen, depth)
        for value in values:
            if is_error(value):
                return value, None
        if len(values) != 1:
            return VALUE_ERROR, None
        return None, values[0]

    def _scalar_or(self, node: Any, sheet: Optional[str], seen: set, depth: int) -> Any:
        """One slot's single value, or the error the pooled rule raises.

        The client engine's ``scalarize``: a matrix is ``#VALUE!`` unless it
        holds an error cell (which propagates) and a one-cell range is that
        cell. Used where the argument must be a single value -- an IF
        condition or branch, ``NOT``, the ``IFERROR``/``IFNA`` value and
        fallback -- so a nested call that answers a matrix is read the same
        way a range of the same shape is.
        """
        error, value = self._scalar_slot(node, sheet, seen, depth)
        return error if error is not None else value

    @staticmethod
    def _coerce_slot(value: Any) -> Tuple[Optional[str], Optional[float]]:
        """Coerce one scalar slot value, returning ``(error, number)``."""
        if is_error(value):
            return value, None
        try:
            return None, _to_number(value)
        except ValueError as exc:
            message = str(exc)
            return (message if message in ERROR_CODES else VALUE_ERROR), None

    def _double_args(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """ROUND/MOD/POWER/CEILING/FLOOR over raw argument nodes.

        Each slot is reduced and coerced in client order -- the first slot
        first, except ROUND which reads its digits first like the client
        engine's ``roundDigits`` -- so a coercion failure in the earlier slot
        beats an error cell in a later range. The pooled propagation in
        ``_call`` cannot express that: it scans the flattened values first
        and always answers the error cell.
        """
        if len(args) != 2:
            return VALUE_ERROR
        if name in ("ROUND", "ROUNDUP", "ROUNDDOWN"):
            error, raw_digits = self._scalar_slot(args[1], sheet, seen, depth)
            if error is not None:
                return error
            error, digits_number = self._coerce_slot(raw_digits)
            if error is not None or digits_number is None:
                return error or VALUE_ERROR
            error, raw_number = self._scalar_slot(args[0], sheet, seen, depth)
            if error is not None:
                return error
            error, number = self._coerce_slot(raw_number)
            if error is not None or number is None:
                return error or VALUE_ERROR
            try:
                digits = int(digits_number)
            except (ValueError, OverflowError) as exc:
                message = str(exc)
                return message if message in ERROR_CODES else VALUE_ERROR
            digits = max(-15, min(digits, 15))
            factor = 10**digits
            scaled = number * factor
            magnitude = abs(scaled)
            epsilon = min(magnitude * 1e-12, 1e-9)
            if name == "ROUND":
                rounded = math.floor(magnitude + 0.5 + epsilon)
            elif name == "ROUNDUP":
                rounded = math.ceil(magnitude - epsilon)
            else:
                rounded = math.floor(magnitude + epsilon)
            return math.copysign(rounded, scaled) / factor
        error, raw_a = self._scalar_slot(args[0], sheet, seen, depth)
        if error is not None:
            return error
        error, a = self._coerce_slot(raw_a)
        if error is not None or a is None:
            return error or VALUE_ERROR
        error, raw_b = self._scalar_slot(args[1], sheet, seen, depth)
        if error is not None:
            return error
        error, b = self._coerce_slot(raw_b)
        if error is not None or b is None:
            return error or VALUE_ERROR
        if name == "MOD":
            if b == 0:
                return DIV_ZERO
            return a - b * math.floor(a / b)
        if name == "POWER":
            if a == 0 and b < 0:
                return DIV_ZERO
            try:
                return math.pow(a, b)
            except (ValueError, OverflowError):
                return NUM_ERROR
        if name in ("CEILING", "FLOOR"):
            if b == 0:
                return DIV_ZERO
            if a > 0 and b < 0:
                return NUM_ERROR
            if a == 0:
                return 0.0
            same_sign = (a < 0) == (b < 0)
            away_from_zero = same_sign == (name == "CEILING")
            magnitude = abs(a) / abs(b)
            epsilon = min(magnitude * 1e-12, 1e-9)
            if away_from_zero:
                multiple = math.ceil(magnitude - epsilon)
            else:
                multiple = math.floor(magnitude + epsilon)
            result = multiple * abs(b)
            if a < 0:
                result = -result
            return float(f"{result:.15g}")
        return VALUE_ERROR
