"""Numeric coercion, comparison and formatting helpers."""

import math
from typing import Any, List, Optional
from ..._formula_shared import (
    ERROR_CODES,
    NUMERIC_TEXT_RE,
    NUM_ERROR,
    VALUE_ERROR,
    _js_number_text,
    _text_equals,
    _to_number,
    is_error,
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


# The largest value Excel still treats as an exact integer. GCD and LCM
# reject anything above it, and LCM rejects a result above it, rather than
# returning a number double-precision arithmetic can no longer represent
# exactly. Mirrors the client engine's MAX_EXACT_INTEGER.
MAX_EXACT_INTEGER = 2**53

# Excel's upper bound for each TIME() component; anything above it is #NUM!.
MAX_TIME_COMPONENT = 32767


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


def _lookup_vector(matrix: List[List[Any]]) -> List[Any]:
    """The 1-D vector a MATCH/LOOKUP/XLOOKUP search reads.

    The client engine's ``lookupVector``: a single row is a *horizontal*
    vector (every cell of that row), anything taller is searched by its first
    column the way VLOOKUP keys on its first column. Taking column 0 of a
    one-row matrix read only its first cell, so a horizontal vector collapsed
    to one element -- MATCH(2, FREQUENCY(A1:A5, A1:A5)) answered 1 here where
    the client answers 4, and every one-row range read the same way.
    """
    if len(matrix) == 1:
        return list(matrix[0])
    return [row[0] if row else None for row in matrix]


def _flatten_argument(value: Any) -> List[Any]:
    """A whole range argument as a flat list, whatever shape it arrived in."""
    if isinstance(value, (list, tuple)):
        out: List[Any] = []
        for item in value:
            out.extend(_flatten_argument(item))
        return out
    return [value]


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


def _to_int32(number: float) -> int:
    """JavaScript's ``ToInt32`` for the bitwise operations.

    The client engine evaluates ``BITAND``/``BITOR``/``BITXOR`` with the
    ``&``/``|``/``^`` operators, which wrap past 2**31 (so a large input can
    answer negative), where Python's integers never wrap.
    """
    wrapped = int(number) % 2**32
    return wrapped - 2**32 if wrapped >= 2**31 else wrapped
