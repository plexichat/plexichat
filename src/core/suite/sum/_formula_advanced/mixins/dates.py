"""Formula evaluator mixin: date/time functions."""

import datetime
from typing import Any, List
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    ERROR_CODES,
    MAX_DATE_SERIAL,
    MAX_NETWORKDAYS_SPAN_DAYS,
    MAX_WORKDAY_OFFSET_DAYS,
    MIN_DATE_SERIAL,
    NUM_ERROR,
    VALUE_ERROR,
    _DATE_EPOCH,
    _to_number,
)
from ..helpers.dates import (
    _WEEK_START_DAYS,
    _date_part,
    _holiday_serials,
    _round_serial,
)


class DateMixin(AdvancedEvaluatorBase):
    def _date(self, name: str, values: List[Any]) -> Any:
        """DATE / YEAR / MONTH / DAY over Excel serial dates."""
        if name == "DATE":
            if len(values) != 3:
                return VALUE_ERROR
            try:
                year = int(_to_number(values[0]))
                month = int(_to_number(values[1]))
                day = int(_to_number(values[2]))
            except ValueError as exc:
                return str(exc)
            # Excel rejects a year outside 0..9999, and adds 1900 to a year in
            # 0..1899 (DATE(108,1,2) is 2008-01-02). Without the second rule a
            # small year built a date in the year itself instead of in the
            # 1900s, which is what the client engine (and Excel) report.
            if year < 0 or year > 9999:
                return NUM_ERROR
            if year < 1900:
                year += 1900
            # Excel normalizes out-of-range months (13 -> January next year)
            # and day overflow via date arithmetic.
            total_months = year * 12 + (month - 1)
            normalized_year = total_months // 12
            normalized_month = total_months % 12 + 1
            if normalized_year < 1:
                return NUM_ERROR
            try:
                base = datetime.date(normalized_year, normalized_month, 1)
                serial = (base + datetime.timedelta(days=day - 1) - _DATE_EPOCH).days
            except (ValueError, OverflowError):
                return NUM_ERROR
            return float(serial)
        if len(values) != 1:
            return VALUE_ERROR
        try:
            serial = int(_round_serial(_to_number(values[0])))
        except ValueError as exc:
            return str(exc)
        try:
            date_value = _DATE_EPOCH + datetime.timedelta(days=serial)
        except (ValueError, OverflowError):
            return NUM_ERROR
        if name == "YEAR":
            return float(date_value.year)
        if name == "MONTH":
            return float(date_value.month)
        if name == "DAY":
            return float(date_value.day)
        return VALUE_ERROR

    def _days(self, values: List[Any]) -> Any:
        """DAYS(end_date, start_date) -> whole-day difference."""
        if len(values) != 2:
            return VALUE_ERROR
        try:
            end = int(_round_serial(_to_number(values[0])))
            start = int(_round_serial(_to_number(values[1])))
        except ValueError as exc:
            return str(exc)
        return float(end - start)

    def _advanced_date(self, name: str, values: List[Any]) -> Any:
        """EOMONTH, EDATE, WEEKDAY, WEEKNUM, NETWORKDAYS, WORKDAY."""
        if name == "EOMONTH":
            # EOMONTH(start_date, months)
            if len(values) != 2:
                return VALUE_ERROR
            try:
                start_serial = int(_round_serial(_to_number(values[0])))
                months = int(_to_number(values[1]))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            try:
                start_date = _DATE_EPOCH + datetime.timedelta(days=start_serial)
                # Add months
                month = start_date.month + months
                year = start_date.year + (month - 1) // 12
                month = (month - 1) % 12 + 1
                # The last day of the month, from the month lengths directly:
                # building the first of the *next* month and stepping back one
                # day ran off the end of December 9999 (there is no
                # 10000-01-01) and answered #NUM! for EOMONTH(2958465, 0).
                leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
                last_day = [
                    31,
                    29 if leap else 28,
                    31,
                    30,
                    31,
                    30,
                    31,
                    31,
                    30,
                    31,
                    30,
                    31,
                ][month - 1]
                end_date = datetime.date(year, month, last_day)
            except (ValueError, OverflowError):
                # A serial (or month offset) that names no representable date
                # is #NUM!, the way Excel reports it -- and the way the rest of
                # this module already handles a missing date.
                return NUM_ERROR
            return float((end_date - _DATE_EPOCH).days)
        elif name == "EDATE":
            # EDATE(start_date, months)
            if len(values) != 2:
                return VALUE_ERROR
            try:
                start_serial = int(_round_serial(_to_number(values[0])))
                months = int(_to_number(values[1]))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            try:
                start_date = _DATE_EPOCH + datetime.timedelta(days=start_serial)
                month = start_date.month + months
                year = start_date.year + (month - 1) // 12
                month = (month - 1) % 12 + 1
                day = min(
                    start_date.day,
                    [
                        31,
                        29
                        if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
                        else 28,
                        31,
                        30,
                        31,
                        30,
                        31,
                        31,
                        30,
                        31,
                        30,
                        31,
                    ][month - 1],
                )
                end_date = datetime.date(year, month, day)
            except (ValueError, OverflowError):
                # A serial (or month offset) that names no representable date
                # is #NUM!, not an OverflowError escaping the engine.
                return NUM_ERROR
            return float((end_date - _DATE_EPOCH).days)
        elif name == "WEEKDAY":
            # WEEKDAY(serial_number, [return_type])
            if len(values) < 1 or len(values) > 2:
                return VALUE_ERROR
            try:
                serial = int(_round_serial(_to_number(values[0])))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            return_type = 1
            if len(values) == 2:
                try:
                    return_type = int(_to_number(values[1]))
                except (ValueError, OverflowError) as exc:
                    return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            try:
                date_val = _DATE_EPOCH + datetime.timedelta(days=serial)
            except (ValueError, OverflowError):
                # A serial outside the representable date range is #NUM!.
                return NUM_ERROR
            weekday = date_val.isoweekday()  # 1=Monday, 7=Sunday
            if return_type == 3:  # 0=Monday, 6=Sunday
                return float(weekday - 1)
            # Types 1 and 17 are Sunday-first, 2 and 11 Monday-first, 12
            # Tuesday-first, ... 16 Saturday-first. _WEEK_START_DAYS holds the
            # Sunday-based index of each type's first day (WEEKNUM uses the
            # same table). An unknown type was previously accepted and answered
            # the raw isoweekday() instead of #NUM!.
            start_day = _WEEK_START_DAYS.get(return_type)
            if start_day is None:
                return NUM_ERROR
            dow0 = weekday % 7  # 0=Sunday .. 6=Saturday
            return float((dow0 - start_day) % 7 + 1)
        elif name == "WEEKNUM":
            # WEEKNUM(serial_number, [return_type])
            if len(values) < 1 or len(values) > 2:
                return VALUE_ERROR
            try:
                number = _to_number(values[0])
                return_type = int(_to_number(values[1])) if len(values) == 2 else 1
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            serial = _date_part(number)
            if serial is None:
                return NUM_ERROR
            date_val = _DATE_EPOCH + datetime.timedelta(days=serial)
            # Type 21 is ISO-8601: week 1 holds the year's first Thursday.
            if return_type == 21:
                return float(date_val.isocalendar()[1])
            start_day = _WEEK_START_DAYS.get(return_type)
            if start_day is None:
                return NUM_ERROR
            # Every other type numbers from the week containing January 1 of
            # the date's own year, differing only in the first day of the
            # week. ``isoweekday() % 7`` re-indexes Monday..Sunday onto
            # Sunday-first 0..6 to match ``start_day``. The week start is
            # subtracted as an integer day ordinal rather than as a date:
            # the week holding a January 1 can begin in the previous year (or
            # before 0001-01-01 for a year-1 date), and a timedelta there left
            # the representable range and escaped as an OverflowError.
            day_ord = date_val.toordinal()
            jan1 = datetime.date(date_val.year, 1, 1)
            jan1_ord = jan1.toordinal()
            week_start = day_ord - ((date_val.isoweekday() % 7 - start_day) % 7)
            first_week_start = jan1_ord - ((jan1.isoweekday() % 7 - start_day) % 7)
            return float((week_start - first_week_start) // 7 + 1)
        elif name == "NETWORKDAYS":
            # NETWORKDAYS(start_date, end_date, [holidays])
            # A holiday range is flattened into ``values`` after the two
            # required arguments, so only the lower bound is fixed here.
            if len(values) < 2:
                return VALUE_ERROR
            try:
                start_part = _date_part(_to_number(values[0]))
                end_part = _date_part(_to_number(values[1]))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            if start_part is None or end_part is None:
                return NUM_ERROR
            # Bound the day-by-day loop: NETWORKDAYS(1, 10**9) would otherwise
            # stall the evaluating thread for hours on a read path.
            if abs(end_part - start_part) > MAX_NETWORKDAYS_SPAN_DAYS:
                return NUM_ERROR
            holidays = _holiday_serials(values[2:])
            # A reversed span counts negative, as Excel does: swap the ends,
            # count the workdays forward, then negate. A holiday counts the
            # same day either way, so only the sign changes.
            sign = 1
            if start_part > end_part:
                start_part, end_part = end_part, start_part
                sign = -1
            # Walk the day serials by index rather than by adding a day to a
            # ``date``: the last day (9999-12-31) has no successor, so the
            # increment after it raised an OverflowError straight out of the
            # evaluator instead of answering the weekday count.
            count = 0
            for serial in range(start_part, end_part + 1):
                # Monday-Friday, excluding listed holidays. A holiday that
                # lands on a weekend is already excluded, so it cannot
                # subtract twice.
                if (
                    _DATE_EPOCH + datetime.timedelta(days=serial)
                ).isoweekday() <= 5 and serial not in holidays:
                    count += 1
            return float(sign * count)
        elif name == "WORKDAY":
            # WORKDAY(start_date, days, [holidays])
            if len(values) < 2:
                return VALUE_ERROR
            try:
                start_part = _date_part(_to_number(values[0]))
                work_days = int(_to_number(values[1]))
            except (ValueError, OverflowError) as exc:
                return str(exc) if str(exc) in ERROR_CODES else VALUE_ERROR
            if start_part is None:
                return NUM_ERROR
            # A negative day count walks backwards, as every other
            # spreadsheet does; zero days is the start date itself.
            remaining = abs(work_days)
            step = 1 if work_days >= 0 else -1
            # Bound the walk: WORKDAY(1, 10**9) would otherwise loop
            # unbounded on a read path.
            if remaining > MAX_WORKDAY_OFFSET_DAYS:
                return NUM_ERROR
            holidays = _holiday_serials(values[2:])
            current = start_part
            while remaining > 0:
                current += step
                # The walk can leave the representable span (past 9999-12-31,
                # or before 0001-01-01); the result names no date, so it is
                # #NUM! rather than an OverflowError or an out-of-range
                # serial.
                if current < MIN_DATE_SERIAL or current > MAX_DATE_SERIAL:
                    return NUM_ERROR
                # Monday-Friday, excluding listed holidays.
                if (
                    _DATE_EPOCH + datetime.timedelta(days=current)
                ).isoweekday() <= 5 and current not in holidays:
                    remaining -= 1
            return float(current)
        return VALUE_ERROR
