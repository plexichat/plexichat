"""Formula evaluator mixin: complex-number functions."""

import math
from typing import Any, List
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    DIV_ZERO,
    NAME_ERROR,
    NUM_ERROR,
    VALUE_ERROR,
    _to_text,
    is_error,
)
from ..helpers.complex_math import (
    _complex_cos,
    _complex_cosh,
    _complex_divide,
    _complex_sin,
    _complex_sinh,
    _format_complex,
    _overflowing,
    _parse_complex,
)
from ..helpers.numbers import _read_number


class ComplexMixin(AdvancedEvaluatorBase):
    def _complex(self, name: str, values: List[Any]) -> Any:
        """Excel's complex-number family, mirroring the client engine.

        COMPLEX builds a value; an IM* function reads one (or two) complex
        arguments, keeps the ``i``/``j`` suffix of the first, and prints the
        result in Excel's complex-number text form.
        """
        if name == "COMPLEX":
            if not 1 <= len(values) <= 3:
                return VALUE_ERROR
            error, real_part = _read_number(values[0])
            if error is not None:
                return error
            imaginary_part = 0.0
            if len(values) >= 2:
                error, imaginary_part = _read_number(values[1])
                if error is not None:
                    return error
            suffix = "i"
            if len(values) == 3:
                suffix = _to_text(values[2]).lower()
                if suffix not in ("i", "j"):
                    return VALUE_ERROR
            return _format_complex(real_part, imaginary_part, suffix)

        if name in ("IMSUM", "IMPRODUCT"):
            if not values:
                return VALUE_ERROR
            real_total, imaginary_total = (0.0, 0.0) if name == "IMSUM" else (1.0, 0.0)
            suffix = "i"
            for index, value in enumerate(values):
                if is_error(value):
                    return value
                parsed = _parse_complex(value)
                if parsed is None:
                    return NUM_ERROR
                if index == 0:
                    suffix = parsed[2]
                if name == "IMSUM":
                    real_total += parsed[0]
                    imaginary_total += parsed[1]
                else:
                    real_total, imaginary_total = (
                        real_total * parsed[0] - imaginary_total * parsed[1],
                        real_total * parsed[1] + imaginary_total * parsed[0],
                    )
            return _format_complex(real_total, imaginary_total, suffix)

        if name in ("IMABS", "IMREAL", "IMAGINARY", "IMCONJUGATE", "IMARGUMENT"):
            if len(values) != 1:
                return VALUE_ERROR
            if is_error(values[0]):
                return values[0]
            parsed = _parse_complex(values[0])
            if parsed is None:
                return NUM_ERROR
            if name == "IMABS":
                return math.hypot(parsed[0], parsed[1])
            if name == "IMREAL":
                return parsed[0]
            if name == "IMAGINARY":
                return parsed[1]
            if name == "IMCONJUGATE":
                return _format_complex(parsed[0], -parsed[1], parsed[2])
            if parsed[0] == 0 and parsed[1] == 0:
                return DIV_ZERO
            return math.atan2(parsed[1], parsed[0])

        if name in ("IMSUB", "IMDIV", "IMMOD"):
            if len(values) != 2:
                return VALUE_ERROR
            if is_error(values[0]):
                return values[0]
            first = _parse_complex(values[0])
            if first is None:
                return NUM_ERROR
            if is_error(values[1]):
                return values[1]
            second = _parse_complex(values[1])
            if second is None:
                return NUM_ERROR
            if name == "IMSUB":
                return _format_complex(
                    first[0] - second[0], first[1] - second[1], first[2]
                )
            quotient = _complex_divide(first, second)
            if quotient is None:
                return DIV_ZERO
            if name == "IMDIV":
                return _format_complex(quotient[0], quotient[1], first[2])
            if not (math.isfinite(quotient[0]) and math.isfinite(quotient[1])):
                # ``math.trunc`` of an infinity raises rather than answering.
                return NUM_ERROR
            quotient_real = math.trunc(quotient[0])
            quotient_imaginary = math.trunc(quotient[1])
            return _format_complex(
                first[0] - (quotient_real * second[0] - quotient_imaginary * second[1]),
                first[1] - (quotient_real * second[1] + quotient_imaginary * second[0]),
                first[2],
            )

        if len(values) != 1:
            return VALUE_ERROR
        if is_error(values[0]):
            return values[0]
        parsed = _parse_complex(values[0])
        if parsed is None:
            return NUM_ERROR
        if name == "IMEXP":
            magnitude = _overflowing(math.exp, parsed[0])
            result = (magnitude * math.cos(parsed[1]), magnitude * math.sin(parsed[1]))
        elif name == "IMLOG":
            magnitude = math.hypot(parsed[0], parsed[1])
            if magnitude == 0:
                return NUM_ERROR
            result = (math.log(magnitude), math.atan2(parsed[1], parsed[0]))
        elif name == "IMSQRT":
            radius = math.sqrt(math.hypot(parsed[0], parsed[1]))
            theta = math.atan2(parsed[1], parsed[0]) / 2
            result = (radius * math.cos(theta), radius * math.sin(theta))
        elif name in ("IMCOS", "IMSIN", "IMTAN", "IMCSC", "IMSEC", "IMCOT"):
            sine = _complex_sin(parsed)
            cosine = _complex_cos(parsed)
            result = {
                "IMCOS": cosine,
                "IMSIN": sine,
                "IMTAN": _complex_divide(sine, cosine),
                "IMCSC": _complex_divide((1.0, 0.0), sine),
                "IMSEC": _complex_divide((1.0, 0.0), cosine),
                "IMCOT": _complex_divide(cosine, sine),
            }[name]
            if result is None:
                return DIV_ZERO
        elif name in ("IMCOSH", "IMSINH", "IMTANH", "IMCSCH", "IMSECH", "IMCOTH"):
            hyperbolic_sine = _complex_sinh(parsed)
            hyperbolic_cosine = _complex_cosh(parsed)
            result = {
                "IMCOSH": hyperbolic_cosine,
                "IMSINH": hyperbolic_sine,
                "IMTANH": _complex_divide(hyperbolic_sine, hyperbolic_cosine),
                "IMCSCH": _complex_divide((1.0, 0.0), hyperbolic_sine),
                "IMSECH": _complex_divide((1.0, 0.0), hyperbolic_cosine),
                "IMCOTH": _complex_divide(hyperbolic_cosine, hyperbolic_sine),
            }[name]
            if result is None:
                return DIV_ZERO
        else:
            return NAME_ERROR
        return _format_complex(result[0], result[1], parsed[2])
