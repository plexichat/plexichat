"""Text slicing functions (LEFT/RIGHT/MID)."""

from typing import Any, List

from .._formula_shared import (
    ERROR_CODES,
    VALUE_ERROR,
    _to_number,
    _to_text,
    _utf16_len,
    _utf16_slice,
    is_error,
)
from .base import FormulaEvaluatorCoreBase


class TextFunctionsMixin(FormulaEvaluatorCoreBase):
    """UTF-16-correct text slicing."""

    def _text_slice(self, name: str, values: List[Any]) -> Any:
        """LEFT, RIGHT, MID with Excel's argument contract.

        ``num_chars`` is optional for LEFT/RIGHT (Excel defaults it to 1) and
        must not be negative; MID additionally requires a 1-based
        ``start_num``. Clamping a negative count to zero instead of failing
        sliced from the wrong end and returned a plausible-looking result
        (``LEFT("hello", -1)`` gave ``"hell"``), hiding the caller's
        mistake instead of surfacing #VALUE!.
        """
        if name == "MID":
            if len(values) != 3:
                return VALUE_ERROR
        elif len(values) < 1 or len(values) > 2:
            return VALUE_ERROR
        if is_error(values[0]):
            return values[0]
        try:
            text = _to_text(values[0])
            if name == "MID":
                start_number = _to_number(values[1])
                count_number = _to_number(values[2])
            else:
                start_number = 1.0
                count_number = _to_number(values[1]) if len(values) == 2 else 1.0
        except (ValueError, OverflowError) as exc:
            return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
        # Test the raw count: Excel rejects a negative one even when it
        # truncates to zero, and the client engine checks the argument before
        # slicing. ``int(-0.5)`` is 0, so checking after the cast let
        # ``LEFT(text, -0.5)`` slice from the start where the client answers
        # #VALUE!.
        if start_number < 1 or count_number < 0:
            return VALUE_ERROR
        start = int(start_number)
        num_chars = int(count_number)
        # Positions and counts are UTF-16 code units, as in Excel and the
        # client engine: LEFT("😀", 1) keeps the lone high surrogate rather
        # than the whole character Python slicing would keep.
        if name == "LEFT":
            return _utf16_slice(text, 0, num_chars)
        if name == "RIGHT":
            # Python's text[-0:] is the whole string, so zero needs its own
            # branch to match the empty result Excel returns.
            if not num_chars:
                return ""
            # A count past the length takes the whole string. Passing the
            # negative ``len - num_chars`` straight to the slice read from the
            # end again instead of clamping to the start, so RIGHT("abcdef",
            # 10) returned the last few characters where Excel -- and the
            # client engine's ``slice(-count)`` -- answer the whole string.
            start = max(_utf16_len(text) - num_chars, 0)
            return _utf16_slice(text, start, None)
        offset = start - 1
        return _utf16_slice(text, offset, offset + num_chars)
