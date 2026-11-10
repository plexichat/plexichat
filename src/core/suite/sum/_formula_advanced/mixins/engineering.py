"""Formula evaluator mixin: engineering functions."""

import math
from typing import Any, List
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    NAME_ERROR,
    NUM_ERROR,
    N_A_ERROR,
    VALUE_ERROR,
    is_error,
)
from ..helpers.engineering import (
    MAX_BESSEL_ORDER,
    _BASE_CONVERSION_FUNCTIONS,
    _CONVERT_UNITS,
    _bessel_j,
    _bessel_y0_series,
    _parse_base,
    _to_base,
)
from ..helpers.numbers import _finite, _js_string, _read_number, _to_int32
from ..helpers.statistics import _erf_single


class EngineeringMixin(AdvancedEvaluatorBase):
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
        if name in _BASE_CONVERSION_FUNCTIONS and is_error(values[0]):
            # The input is scalarized before the places are coerced, the way
            # the client reads ``String(scalarize(args[0]))`` first: a range
            # holding an error cell propagates ahead of a bad places value.
            return values[0]
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
            if x <= 0 or n < 0 or n > MAX_BESSEL_ORDER:
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
