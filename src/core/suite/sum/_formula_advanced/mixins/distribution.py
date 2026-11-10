"""Formula evaluator mixin: distributions and combinatorics."""

import math
from typing import Any, List, Optional
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    MAX_LOOP_ITERATIONS,
    NUM_ERROR,
    VALUE_ERROR,
)
from ..helpers.numbers import _finite, _literal_error, _number_values, _pow_js
from ..helpers.statistics import (
    _betai,
    _combin,
    _factorial,
    _gamma_p,
    _gammaln,
    _gaussian,
    _inverse_normal,
)


class DistributionMixin(AdvancedEvaluatorBase):
    def _distribution(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """The probability distributions and their inverses."""
        values = self._argument_list(args, sheet, seen, depth)
        if name == "NORMDIST":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:3])
            if error is not None:
                return error
            x, mean, sd = numbers
            cumulative = self._truthy(values[3])
            if sd <= 0:
                return NUM_ERROR
            z = (x - mean) / sd
            if cumulative:
                return _finite(_gaussian(z))
            return _finite(
                math.exp(-0.5 * _pow_js(z, 2)) / (sd * math.sqrt(2 * math.pi))
            )
        if name == "NORMSDIST":
            if len(values) != 1:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            return _finite(_gaussian(numbers[0]))
        if name in ("NORMINV", "NORMSINV", "NORMINVS"):
            if name == "NORMINV":
                if len(values) != 3:
                    return VALUE_ERROR
                error, numbers = self._number_arguments(values)
                if error is not None:
                    return error
                probability, mean, sd = numbers
            else:
                if len(values) != 1:
                    return VALUE_ERROR
                error, numbers = self._number_arguments(values)
                if error is not None:
                    return error
                probability, mean, sd = numbers[0], 0.0, 1.0
            if probability <= 0 or probability >= 1 or sd <= 0:
                return NUM_ERROR
            return _finite(mean + sd * _inverse_normal(probability))
        if name == "BINOMDIST":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:3])
            if error is not None:
                return error
            successes = math.floor(numbers[0])
            trials = math.floor(numbers[1])
            probability = numbers[2]
            cumulative = self._truthy(values[3])
            if successes < 0 or trials < 0 or probability < 0 or probability > 1:
                return NUM_ERROR
            if successes > trials:
                # Excel reports #NUM! when the successes exceed the trials;
                # answering 0 made a nonsense argument look like a real result.
                return NUM_ERROR
            if cumulative:
                if successes > MAX_LOOP_ITERATIONS:
                    # One term per success; a count large enough to make that
                    # unbounded work is #NUM! rather than a hang.
                    return NUM_ERROR
                total = 0.0
                # ``_combin(trials, i)`` rebuilds its running product from
                # scratch on every call, which makes the sum quadratic. Walk
                # the same product once and round each step exactly as
                # ``_combin`` does, so every term stays bit-identical while
                # each iteration is O(1).
                coefficient = 1.0
                for i in range(successes + 1):
                    if i > 0:
                        coefficient = coefficient * (trials - (i - 1)) / i
                    rounded = (
                        float(math.floor(coefficient + 0.5))
                        if math.isfinite(coefficient)
                        else float("inf")
                    )
                    total += (
                        rounded
                        * _pow_js(probability, i)
                        * _pow_js(1 - probability, trials - i)
                    )
                return _finite(total)
            return _finite(
                _combin(trials, successes)
                * _pow_js(probability, successes)
                * _pow_js(1 - probability, trials - successes)
            )
        if name == "POISSON":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:2])
            if error is not None:
                return error
            events = math.floor(numbers[0])
            mean = numbers[1]
            cumulative = self._truthy(values[2])
            if events < 0 or mean <= 0:
                return NUM_ERROR
            if cumulative:
                if events > MAX_LOOP_ITERATIONS:
                    # One term per event; a count large enough to make that
                    # unbounded work is #NUM! rather than a hang.
                    return NUM_ERROR
                total = 0.0
                # ``_factorial`` rebuilds the product up to ``i`` on every
                # term, which makes the sum quadratic in ``i``. Carry the same
                # product across the loop instead: ``fact(i)`` is exactly that
                # running product, so every term stays bit-identical while
                # each iteration is O(1).
                factorial = 1.0
                for i in range(events + 1):
                    if i >= 2:
                        factorial *= i
                        if not math.isfinite(factorial):
                            factorial = float("inf")
                    total += math.exp(-mean) * _pow_js(mean, i) / factorial
                return _finite(total)
            return _finite(math.exp(-mean) * _pow_js(mean, events) / _factorial(events))
        if name == "EXPONDIST":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:2])
            if error is not None:
                return error
            x, lambda_ = numbers
            cumulative = self._truthy(values[2])
            if x < 0 or lambda_ <= 0:
                return NUM_ERROR
            if cumulative:
                return _finite(1 - math.exp(-lambda_ * x))
            return _finite(lambda_ * math.exp(-lambda_ * x))
        if name == "WEIBULL":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:3])
            if error is not None:
                return error
            x, alpha, beta = numbers
            cumulative = self._truthy(values[3])
            if x < 0 or alpha <= 0 or beta <= 0:
                return NUM_ERROR
            if cumulative:
                return _finite(1 - math.exp(-_pow_js(x / beta, alpha)))
            return _finite(
                alpha
                / beta
                * _pow_js(x / beta, alpha - 1)
                * math.exp(-_pow_js(x / beta, alpha))
            )
        if name == "GAMMALN":
            if len(values) != 1:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            if numbers[0] <= 0:
                return NUM_ERROR
            return _finite(_gammaln(numbers[0]))
        if name == "GAMMADIST":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values[:3])
            if error is not None:
                return error
            x, alpha, beta = numbers
            cumulative = self._truthy(values[3])
            if x < 0 or alpha <= 0 or beta <= 0:
                return NUM_ERROR
            if cumulative:
                return _finite(_gamma_p(alpha, x / beta))
            if x == 0:
                return _finite((1 / beta) if alpha == 1 else 0.0)
            return _finite(
                math.exp(
                    -(x / beta) + (alpha - 1) * math.log(x / beta) - _gammaln(alpha)
                )
                / beta
            )
        if name == "GAMMAINV":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            probability, alpha, beta = numbers
            if probability <= 0 or probability >= 1 or alpha <= 0 or beta <= 0:
                return NUM_ERROR
            lo = 0.0
            hi = beta * max(1.0, alpha)
            guard = 0
            while _gamma_p(alpha, hi / beta) < probability and guard < 60:
                hi *= 2
                guard += 1
            if guard >= 60:
                return NUM_ERROR
            for _ in range(100):
                mid = (lo + hi) / 2
                if _gamma_p(alpha, mid / beta) < probability:
                    lo = mid
                else:
                    hi = mid
                if hi - lo < 1e-10 * max(1.0, abs(mid)):
                    break
            return _finite((lo + hi) / 2)
        if name in ("BETADIST", "BETAINV"):
            if len(values) < 3 or len(values) > 5:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            first, alpha, beta = numbers[0], numbers[1], numbers[2]
            a = numbers[3] if len(values) >= 4 else 0.0
            b = numbers[4] if len(values) >= 5 else 1.0
            if name == "BETADIST":
                if alpha <= 0 or beta <= 0 or b <= a:
                    return NUM_ERROR
                t = (first - a) / (b - a)
                if t <= 0:
                    return 0
                if t >= 1:
                    return 1
                return _finite(_betai(t, alpha, beta))
            if first <= 0 or first >= 1 or alpha <= 0 or beta <= 0 or b <= a:
                return NUM_ERROR
            lo, hi = 0.0, 1.0
            for _ in range(100):
                mid = (lo + hi) / 2
                cdf = _betai(mid, alpha, beta)
                if not math.isfinite(cdf):
                    # An incomplete beta that left the reals cannot be inverted:
                    # the parameters are past what the double arithmetic can
                    # resolve, so answer #NUM! rather than converging on
                    # whatever the overflow left behind (the client engine
                    # makes the same check).
                    return NUM_ERROR
                if cdf < first:
                    lo = mid
                else:
                    hi = mid
                if hi - lo < 1e-12:
                    break
            return _finite(a + (b - a) * ((lo + hi) / 2))
        if name == "NEGBINOMDIST":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            failures = math.floor(numbers[0])
            successes = math.floor(numbers[1])
            probability = numbers[2]
            if failures < 0 or successes < 1 or probability < 0 or probability > 1:
                return NUM_ERROR
            return _finite(
                _combin(failures + successes - 1, successes - 1)
                * _pow_js(probability, successes)
                * _pow_js(1 - probability, failures)
            )
        if name == "FREQUENCY":
            if len(values) != 2:
                return VALUE_ERROR
            # Read the two arrays through ``_expand_arg`` rather than the pooled
            # ``values``, so the provenance of each entry survives: an error
            # *written into* the formula propagates (``FREQUENCY(1/0, 1/0)`` is
            # ``#DIV/0!``, as Excel and the client engine answer) while an error
            # stored in a referenced *cell* is skipped the way text is. Reading
            # the pooled list dropped both, so a computed error answered ``[0]``
            # here where the client propagated it -- a divergence the sweep's
            # matrix-comparison allowance had been hiding.
            data, data_ref = self._expand_arg(args[0], sheet, seen, depth)
            bins, bins_ref = self._expand_arg(args[1], sheet, seen, depth)
            error = _literal_error(data, data_ref) or _literal_error(bins, bins_ref)
            if error is not None:
                return error
            edges = _number_values(bins)
            result = [0] * (len(edges) + 1)
            for value in _number_values(data):
                for index, edge in enumerate(edges):
                    if value <= edge:
                        result[index] += 1
                        break
                else:
                    result[-1] += 1
            return result
        return VALUE_ERROR

    def _combinatorics(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """The counting functions: COMBIN/COMBINA/PERMUT, FACT/FACTDOUBLE and
        MULTINOMIAL."""
        values = self._argument_list(args, sheet, seen, depth)
        if name in ("COMBIN", "COMBINA", "PERMUT"):
            if len(values) != 2:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            n = math.floor(numbers[0])
            k = math.floor(numbers[1])
            if n < 0 or k < 0:
                return NUM_ERROR
            if name == "COMBINA":
                return _finite(_combin(n + k - 1, k))
            if k > n:
                return NUM_ERROR
            if name == "COMBIN":
                return _finite(_combin(n, k))
            result = 1.0
            for i in range(k):
                result *= n - i
                if not math.isfinite(result):
                    return NUM_ERROR
            return result
        if name in ("FACT", "FACTDOUBLE"):
            if len(values) != 1:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            n = math.floor(numbers[0])
            if n < 0:
                return NUM_ERROR
            if name == "FACT":
                return _finite(_factorial(n))
            result = 1.0
            i = n
            while i > 1:
                result *= i
                if not math.isfinite(result):
                    return NUM_ERROR
                i -= 2
            return result
        if name == "MULTINOMIAL":
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            integers = [math.floor(number) for number in numbers]
            if any(value < 0 for value in integers):
                return NUM_ERROR
            result = _factorial(sum(integers))
            for value in integers:
                result /= _factorial(value)
            if not math.isfinite(result):
                return NUM_ERROR
            return float(math.floor(result + 0.5))
        return VALUE_ERROR
