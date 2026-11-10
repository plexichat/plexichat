"""Formula evaluator mixin: financial functions."""

import math
from typing import Any, List, Optional
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    DIV_ZERO,
    MAX_LOOP_ITERATIONS,
    NUM_ERROR,
    VALUE_ERROR,
    _numeric,
)
from ..helpers.dates import _date_serial
from ..helpers.numbers import _finite, _flatten_argument, _pow_js, _read_number


class FinancialMixin(AdvancedEvaluatorBase):
    def _annuity_payment(
        self, rate: float, nper: float, pv: float, fv: float, type_: float
    ) -> Any:
        """PMT(rate, nper, pv, fv, type), shared with IPMT and PPMT."""
        if nper <= 0:
            return NUM_ERROR
        if rate == 0:
            return _finite(-(fv + pv) / nper)
        temp = _pow_js(1 + rate, nper)
        try:
            return _finite(-(pv * temp + fv) / ((temp - 1) * (1 + rate * type_) / rate))
        except ZeroDivisionError:
            return NUM_ERROR

    def _interest_payment(
        self, rate: float, per: float, nper: float, pv: float, fv: float, type_: float
    ) -> Any:
        """IPMT(rate, per, nper, pv, fv, type): the interest part of a payment."""
        payment = self._annuity_payment(rate, nper, pv, fv, type_)
        if isinstance(payment, str):
            return payment
        if rate == 0:
            return 0.0
        try:
            growth = _pow_js(1 + rate, per - 1)
            balance = pv * growth + payment * (1 + rate * type_) * (growth - 1) / rate
            return _finite(balance * rate)
        except ZeroDivisionError:
            return NUM_ERROR

    def _solve_rate(
        self, nper: float, pmt: float, pv: float, fv: float, type_: float, guess: float
    ) -> Any:
        """RATE: Newton-Raphson on the annuity identity, with a bisection fallback.

        A bad guess can NaN or diverge (Excel's RATE is #NUM! then), so a
        bracketed sign change backs up the iteration; that is what lets the
        ordinary annuity case always converge.
        """
        tolerance = 1e-8

        def residual(rate: float) -> float:
            r1 = 1 + rate
            rn = _pow_js(r1, nper)
            if abs(rate) < 1e-10:
                return pv + pmt * nper + fv
            a = (rn - 1) / rate
            return pv * rn + pmt * (1 + rate * type_) * a + fv

        def derivative(rate: float) -> float:
            r1 = 1 + rate
            rn = _pow_js(r1, nper)
            try:
                a = (rn - 1) / rate
                b = (nper * rn / rate - a) / rate
                return pv * nper * rn / r1 + pmt * (type_ * a + (1 + rate * type_) * b)
            except ZeroDivisionError:
                return float("nan")

        rate = guess
        for _ in range(100):
            # A NaN guess fails this test, exactly as it does in the client.
            if not (rate > -1) or rate > 1e6:
                break
            value = residual(rate)
            if abs(value) < tolerance:
                return _finite(rate)
            slope = derivative(rate)
            if abs(slope) < 1e-12:
                break
            new_rate = rate - value / slope
            if abs(new_rate - rate) < tolerance:
                return _finite(new_rate)
            rate = new_rate

        # Bisection fallback: bracket a root and refine it.
        lo = -0.999999
        hi = max(1.0, abs(guess) * 2)
        f_lo = residual(lo)
        for _ in range(80):
            if not (f_lo * residual(hi) > 0):
                break
            hi *= 2
        if f_lo * residual(hi) <= 0:
            for _ in range(200):
                mid = (lo + hi) / 2
                f_mid = residual(mid)
                if abs(f_mid) < tolerance:
                    return _finite(mid)
                if f_lo * f_mid <= 0:
                    hi = mid
                else:
                    lo = mid
                    f_lo = f_mid
            return _finite((lo + hi) / 2)
        return NUM_ERROR

    def _financial(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """The financial functions: annuities, cash-flow series and depreciation."""
        values = self._argument_list(args, sheet, seen, depth)
        if name in ("PMT", "FV", "PV", "NPER"):
            if len(values) < 3 or len(values) > 5:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            first, second, third = numbers[0], numbers[1], numbers[2]
            fourth = numbers[3] if len(values) >= 4 else 0.0
            fifth = numbers[4] if len(values) == 5 else 0.0
            if name == "PMT":
                return self._annuity_payment(first, second, third, fourth, fifth)
            if name == "FV":
                rate, nper, pmt = first, second, third
                pv, type_ = fourth, fifth
                if rate == 0:
                    return _finite(-(pv + pmt * nper))
                temp = _pow_js(1 + rate, nper)
                return _finite(
                    -(pv * temp + pmt * (1 + rate * type_) * (temp - 1) / rate)
                )
            if name == "PV":
                rate, nper, pmt = first, second, third
                fv, type_ = fourth, fifth
                if rate == 0:
                    return _finite(-(pmt * nper + fv))
                temp = _pow_js(1 + rate, nper)
                try:
                    return _finite(
                        -(pmt * (1 + rate * type_) * (temp - 1) / rate + fv) / temp
                    )
                except ZeroDivisionError:
                    return NUM_ERROR
            # NPER
            rate, pmt, pv = first, second, third
            fv, type_ = fourth, fifth
            if rate == 0:
                if pmt == 0:
                    return DIV_ZERO
                return _finite(-(pv + fv) / pmt)
            numerator = pmt * (1 + rate * type_) - fv * rate
            denominator = pv * rate + pmt * (1 + rate * type_)
            if denominator == 0:
                return DIV_ZERO
            try:
                return _finite(math.log(numerator / denominator) / math.log(1 + rate))
            except (ValueError, ZeroDivisionError):
                return NUM_ERROR
        if name == "RATE":
            if len(values) < 3 or len(values) > 6:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            nper, pmt, pv = numbers[0], numbers[1], numbers[2]
            fv = numbers[3] if len(values) >= 4 else 0.0
            type_ = numbers[4] if len(values) >= 5 else 0.0
            guess = numbers[5] if len(values) == 6 else 0.1
            return self._solve_rate(nper, pmt, pv, fv, type_, guess)
        if name in ("IPMT", "PPMT"):
            if len(values) < 4 or len(values) > 6:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            rate, per, nper, pv = numbers[0], numbers[1], numbers[2], numbers[3]
            fv = numbers[4] if len(values) >= 5 else 0.0
            type_ = numbers[5] if len(values) == 6 else 0.0
            if per < 1 or per > nper:
                return NUM_ERROR
            if name == "IPMT":
                return self._interest_payment(rate, per, nper, pv, fv, type_)
            payment = self._annuity_payment(rate, nper, pv, fv, type_)
            if isinstance(payment, str):
                return payment
            interest = self._interest_payment(rate, per, nper, pv, fv, type_)
            if isinstance(interest, str):
                return interest
            return _finite(payment - interest)
        if name == "NPV":
            if len(values) < 2:
                return VALUE_ERROR
            error, rate = _read_number(values[0])
            if error is not None:
                return error
            cashflows: List[float] = []
            for value in values[1:]:
                if isinstance(value, (list, tuple)):
                    # A range contributes only its numbers; Excel ignores text,
                    # logicals and blanks stored inside a reference.
                    for item in _flatten_argument(value):
                        if _numeric(item):
                            cashflows.append(float(item))
                    continue
                error, number = _read_number(value)
                if error is not None:
                    return error
                cashflows.append(number)
            total = 0.0
            for index, cashflow in enumerate(cashflows):
                discount = _pow_js(1 + rate, index + 1)
                # A rate of -1 makes the discount factor (1+rate)**n zero. That
                # is a division by zero rather than an overflow, so it is
                # #DIV/0! -- the answer the engine already gives the same zero
                # denominator in MIRR -- and not the #NUM! an infinite result
                # would earn. Dividing here used to raise ZeroDivisionError out
                # of the engine instead.
                if discount == 0:
                    return DIV_ZERO
                total += cashflow / discount
            return _finite(total)
        if name == "IRR":
            if len(values) < 1 or len(values) > 2:
                return VALUE_ERROR
            guess = 0.1
            if len(values) == 2:
                error, guess = _read_number(values[1])
                if error is not None:
                    return error
            error, cashflows = self._cash_flows(values[0])
            if error is not None:
                return error
            if any(value > 0 for value in cashflows) and any(
                value < 0 for value in cashflows
            ):
                rate = guess
                tolerance = 1e-7
                for _ in range(100):
                    npv = 0.0
                    dnpv = 0.0
                    try:
                        for t, cashflow in enumerate(cashflows):
                            npv += cashflow / _pow_js(1 + rate, t)
                            dnpv -= t * cashflow / _pow_js(1 + rate, t + 1)
                    except ZeroDivisionError:
                        return NUM_ERROR
                    if abs(dnpv) < 1e-12:
                        break
                    new_rate = rate - npv / dnpv
                    if abs(new_rate - rate) < tolerance:
                        return _finite(new_rate)
                    rate = new_rate
            return NUM_ERROR
        if name == "MIRR":
            if len(values) != 3:
                return VALUE_ERROR
            error, finance_rate = _read_number(values[1])
            if error is not None:
                return error
            error, reinvest_rate = _read_number(values[2])
            if error is not None:
                return error
            error, cashflows = self._cash_flows(values[0])
            if error is not None:
                return error
            last = len(cashflows) - 1
            # Excel needs a sign change for a rate to exist at all: at least one
            # negative cash flow (something to discount back) and at least one
            # positive one (something to compound forward). A lone value has
            # neither, and MIRR is #DIV/0! for that as well as for an all-one-sign
            # series; it used to read the sign by testing the two sums, which let
            # a single negative value fall through to the -1 a bare 0/0 makes.
            has_negative = any(cashflow < 0 for cashflow in cashflows)
            has_positive = any(cashflow > 0 for cashflow in cashflows)
            if last <= 0 or not (has_negative and has_positive):
                return DIV_ZERO
            pv_negative = 0.0
            fv_positive = 0.0
            for t, cashflow in enumerate(cashflows):
                if cashflow < 0:
                    discount = _pow_js(1 + finance_rate, t)
                    # A finance rate of -1 zeroes the discount factor of every
                    # period after the first, which is #DIV/0! and not the
                    # ZeroDivisionError this division used to raise.
                    if discount == 0:
                        return DIV_ZERO
                    pv_negative += cashflow / discount
                else:
                    fv_positive += cashflow * _pow_js(1 + reinvest_rate, last - t)
            if pv_negative == 0:
                return DIV_ZERO
            return _finite(_pow_js(-fv_positive / pv_negative, 1 / last) - 1)
        if name == "XNPV":
            if len(values) != 3:
                return VALUE_ERROR
            error, rate = _read_number(values[0])
            if error is not None:
                return error
            amounts: List[float] = []
            for item in _flatten_argument(values[1]):
                error, number = _read_number(item)
                if error is not None:
                    return error
                amounts.append(number)
            serials: List[float] = []
            for item in _flatten_argument(values[2]):
                serial = _date_serial(item)
                if serial is None:
                    return VALUE_ERROR
                serials.append(serial)
            if len(amounts) != len(serials):
                return VALUE_ERROR
            start = serials[0]
            total = 0.0
            for amount, serial in zip(amounts, serials):
                discount = _pow_js(1 + rate, (serial - start) / 365)
                # As in NPV, a rate of -1 zeroes the discount factor of every
                # period after the first, which is #DIV/0! and not #NUM!.
                if discount == 0:
                    return DIV_ZERO
                total += amount / discount
            return _finite(total)
        if name == "SLN":
            if len(values) != 3:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            cost, salvage, life = numbers
            if life <= 0:
                return NUM_ERROR
            return _finite((cost - salvage) / life)
        if name == "SYD":
            if len(values) != 4:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            cost, salvage, life, per = numbers
            if life <= 0 or per < 1 or per > life:
                return NUM_ERROR
            denominator = life * (life + 1) / 2
            if denominator == 0:
                return NUM_ERROR
            return _finite((cost - salvage) * (life - per + 1) / denominator)
        if name == "DB":
            if len(values) < 4 or len(values) > 5:
                return VALUE_ERROR
            error, numbers = self._number_arguments(values)
            if error is not None:
                return error
            cost, salvage, life, period = numbers[0], numbers[1], numbers[2], numbers[3]
            month = numbers[4] if len(values) == 5 else 12.0
            if life <= 0 or cost == 0:
                return NUM_ERROR
            rate = 1 - _pow_js(salvage / cost, 1 / life)
            book_value = cost
            depreciation = 0.0
            # ``i`` is compared against the raw period and life, the way the
            # client's loop does, so a fractional period stops at the same
            # iteration on both engines.
            i = 1
            while i <= period:
                if i > MAX_LOOP_ITERATIONS:
                    # A period long enough to walk this loop for hours is not a
                    # depreciation either engine can answer for; a formula is
                    # untrusted input, so it is #NUM! rather than a hang.
                    return NUM_ERROR
                if i == 1:
                    depreciation = cost * rate * month / 12
                elif i == life + 1:
                    depreciation = book_value * rate * (12 - month) / 12
                else:
                    depreciation = book_value * rate
                book_value -= depreciation
                if depreciation == 0 and i > life + 1:
                    # Past ``life + 1`` every remaining period takes the
                    # ``book_value * rate`` branch, and once that has
                    # underflowed to zero it stays zero: the book no longer
                    # moves, so the remaining periods cannot change the answer
                    # and the loop can stop instead of counting them out one at
                    # a time. The ``life + 1`` branch itself is excluded -- its
                    # ``12 - month`` factor makes it zero for a twelve-month
                    # year, and the periods after it are nonzero again.
                    break
                i += 1
            return _finite(depreciation)
        return VALUE_ERROR

    def _cash_flows(self, value: Any) -> tuple:
        """A cash-flow range as a list of numbers, as the client's toNumber reads it."""
        cashflows: List[float] = []
        for item in _flatten_argument(value):
            error, number = _read_number(item)
            if error is not None:
                return error, None
            cashflows.append(number)
        return None, cashflows
