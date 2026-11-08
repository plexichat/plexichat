"""Normal/gamma/beta/factorial helpers for statistical functions."""

import math


def _gaussian(z: float) -> float:
    """The standard normal CDF (Hart's algorithm)."""
    a1, a2, a3 = 0.254829592, -0.284496736, 1.421413741
    a4, a5, p = -1.453152027, 1.061405429, 0.3275911
    sign = -1.0 if z < 0 else 1.0
    x = abs(z) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return 0.5 * (1.0 + sign * y)


def _inverse_normal(p: float) -> float:
    """The inverse standard normal CDF (Acklam's rational approximation).

    The public distribution functions reject ``p`` outside ``(0, 1)`` before
    calling this helper, but the guard stays here too: a direct caller must
    get ``NaN`` (which ``_finite`` turns into ``#NUM!``) rather than a
    ``math.log`` domain error escaping the evaluator.
    """
    if not math.isfinite(p) or p <= 0.0 or p >= 1.0:
        return float("nan")
    a = [
        -39.6968302866538,
        220.946098424521,
        -275.928510446969,
        138.357751867269,
        -30.6647980661472,
        2.50662827745924,
    ]
    b = [
        -54.4760987982241,
        161.585836858041,
        -155.698979859887,
        66.8013118877197,
        -13.2806815528857,
    ]
    c = [
        -0.00778489400243029,
        -0.322396458041136,
        -2.40075827716184,
        -2.54973253934373,
        4.37466414146497,
        2.93816398269878,
    ]
    d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
        ) / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
    )


def _gammaln(g: float) -> float:
    """Natural log of the gamma function (Lanczos approximation, g > 0)."""
    coefficients = [
        76.18009172947146,
        -86.50532032941677,
        24.01409824083091,
        -1.231739572450155,
        0.1208650973866179e-2,
        -0.5395239384953e-5,
    ]
    x = g
    tmp = x + 5.5
    tmp -= (x + 0.5) * math.log(tmp)
    series = 1.000000000190015
    for coefficient in coefficients:
        x += 1.0
        series += coefficient / x
    return -tmp + math.log(2.5066282746310005 * series / g)


def _gamma_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x)."""
    if x <= 0:
        return 0.0
    if a <= 0:
        return float("nan")
    gln = _gammaln(a)
    if x < a + 1:
        ap = a
        total = 1.0 / a
        delta = total
        for _ in range(1000):
            ap += 1.0
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-12:
                break
        return total * math.exp(-x + a * math.log(x) - gln)
    b = x + 1.0 - a
    c = 1e300
    # JavaScript divides by zero to Infinity rather than raising, and the
    # continued fraction then carries that Infinity into a NaN. Reproduce it
    # there instead of letting the ZeroDivisionError escape: at huge `a` the
    # ``1 - a`` rounds away, so ``b`` is exactly zero and the client answers
    # #NUM! while this engine used to raise.
    d = 1.0 / b if b != 0 else float("inf")
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-300:
            d = 1e-300
        c = b + an / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return 1.0 - math.exp(-x + a * math.log(x) - gln) * h


def _betacf(x: float, a: float, b: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    max_iterations, epsilon, tiny = 200, 3e-12, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def _betai(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        a * math.log(x)
        + b * math.log(1 - x)
        - (_gammaln(a) + _gammaln(b) - _gammaln(a + b))
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(x, a, b) / a
    return 1.0 - front * _betacf(1 - x, b, a) / b


def _factorial(n: int) -> float:
    result = 1.0
    for i in range(2, n + 1):
        result *= i
        if not math.isfinite(result):
            # A factorial past 170 leaves the doubles. Returning as soon as it
            # overflows keeps a huge argument from spinning a loop that cannot
            # produce a representable number.
            return float("inf")
    return result


def _combin(n: int, k: int) -> float:
    """The binomial coefficient, rounded the way the client's Math.round does."""
    result = 1.0
    for i in range(k):
        result = result * (n - i) / (i + 1)
        if not math.isfinite(result):
            return float("inf")
    return float(math.floor(result + 0.5))


def _fact_float(n: int) -> float:
    """``n!`` accumulated in doubles, the way the client series does.

    The factorials feed a division, so an exactly-rounded integer factorial
    divided once can differ from the client's multiply-and-divide chain in the
    last ulp; accumulating here keeps the Bessel terms bit-identical.
    """
    result = 1.0
    for index in range(2, n + 1):
        result *= index
        if not math.isfinite(result):
            # A factorial past 170 leaves the doubles. Returning as soon as it
            # overflows keeps a huge order from spinning a loop that cannot
            # produce a representable number; the loop below would answer the
            # same infinity.
            return float("inf")
    return result


def _erf_single(x: float) -> float:
    """The error function (Abramowitz & Stegun 7.1.26), as the client runs it."""
    sign = -1.0 if x < 0 else 1.0
    ax = abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    y = 1.0 - (
        ((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
        + 0.254829592
    ) * t * math.exp(-ax * ax)
    return sign * y
