"""Function-call dispatch for the formula core."""

import re
from typing import Any, List, Optional, Tuple

from .._formula_shared import (
    DIV_ZERO,
    ERROR_CODES,
    N_A_ERROR,
    NAME_ERROR,
    NUM_ERROR,
    VALUE_ERROR,
    _slot_text,
    _to_text,
    is_error,
    parse_cell_ref,
)
from .arity import _ARITY
from .base import FormulaEvaluatorCoreBase, _DependencyBudgetExceeded
from .tables import (
    _ADOPTED_SINGLE_FUNCTIONS,
    _ADOPTED_STATISTICAL_FUNCTIONS,
    _AGGREGATE_ERROR_FUNCTIONS,
    _BRANCHING_FUNCTIONS,
    _COMBINATORICS_FUNCTIONS,
    _COMPLEX_FUNCTIONS,
    _DISTRIBUTION_FUNCTIONS,
    _EAGER_ARGUMENT_ERROR_EXCLUDED,
    _ENGINEERING_FUNCTIONS,
    _ERROR_TOLERANT_FUNCTIONS,
    _FINANCIAL_FUNCTIONS,
    _FLAT_ARGUMENT_ERROR_FUNCTIONS,
    _SCALAR_SLOT_FUNCTIONS,
    _SCALAR_SLOT_LEAD,
    _STRUCTURED_FUNCTIONS,
    _VALUE_FUNCTIONS,
)


class DispatchMixin(FormulaEvaluatorCoreBase):
    """Resolve a call to its implementation, arity, and error rules."""

    def _call(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """Evaluate a function call, yielding its error value when it fails.

        The client engine evaluates a function body to a *value*: a body that
        fails yields the error it raised, and an enclosing function can then
        classify or skip it (COUNT ignores it, ISERROR reports TRUE, PI ignores
        its arguments). Many bodies here raise ``ValueError`` /
        ``ZeroDivisionError`` / ``OverflowError`` instead, and without this
        wrapper the exception unwound straight past every enclosing call, so
        ``COUNT(ABS("x"))`` was #VALUE! (not 0) and ``PI(ABS("x"))`` was
        #VALUE! (not pi). ``_DependencyBudgetExceeded`` is deliberately *not*
        caught: a runaway cycle has to abort the whole formula rather than
        become a #NUM! an enclosing error-tolerant function could skip.
        """
        try:
            return self._call_impl(name, args, sheet, seen, depth)
        except _DependencyBudgetExceeded:
            raise
        except ZeroDivisionError:
            return DIV_ZERO
        except OverflowError:
            return NUM_ERROR
        except ValueError as exc:
            message = str(exc)
            return message if message in ERROR_CODES else VALUE_ERROR

    def _call_impl(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        # A stray argument is #VALUE! before any argument is evaluated, so a
        # function called with the wrong number of them never lets an error
        # operand decide the answer (see _ARITY). An unknown name is not in
        # the table, so this never turns a #NAME? into #VALUE!.
        arity = _ARITY.get(name)
        if arity is not None:
            low, high, step = arity
            if len(args) < low or (high is not None and len(args) > high):
                return VALUE_ERROR
            if step is not None and len(args) % 2 != step:
                return VALUE_ERROR
        # The client engine evaluates every argument before it calls the
        # function, so an error *argument* propagates in argument order --
        # ahead of an error cell in a later range read, a length mismatch or a
        # coercion failure. The pooled range readers and the lazy/classifying
        # calls read their arguments for themselves and are excluded.
        if name in _ARITY and name not in _EAGER_ARGUMENT_ERROR_EXCLUDED:
            error = self._argument_error(args, sheet, seen, depth)
            if error is not None:
                return error
        elif name in _FLAT_ARGUMENT_ERROR_FUNCTIONS:
            # A flat range reader defers *every* reference -- a ``:`` range
            # and a single cell alike -- to read the cells itself, but it
            # still evaluates its scalar arguments eagerly and propagates an
            # error among them in argument order. RANK("x", 1/0) is #DIV/0!
            # here and in the client engine, where reading only the body left
            # the first slot's #VALUE! to win.
            error = self._argument_error(
                args, sheet, seen, depth, defer_references=True
            )
            if error is not None:
                return error
        # Evaluate the arguments lazily where the function is short-circuiting.
        if name == "IF":
            # Excel's third argument is optional: IF(test, value_if_true) is
            # legal and a false test yields FALSE. Requiring exactly three
            # arguments made that form -- and any arithmetic on it -- #VALUE!.
            if len(args) not in (2, 3):
                return VALUE_ERROR
            # The condition is inspected for an error *before* it is coerced to
            # a boolean. ``_truthy`` returns a plain bool, so the old guard
            # could never fire and IF(1/0, 1, 2) took the true branch -- a
            # non-empty error string is truthy -- instead of returning
            # #DIV/0!. The condition and the chosen branch are single values,
            # so a matrix -- whether a range or a nested call -- is read through
            # ``_scalar_or`` the way the client engine's ``scalarize`` reads it
            # (IF(1, FREQUENCY(A1:A5, A1:A5)) is #VALUE!).
            condition = self._scalar_or(args[0], sheet, seen, depth)
            if is_error(condition):
                return condition
            if self._truthy(condition):
                return self._scalar_or(args[1], sheet, seen, depth)
            if len(args) == 3:
                return self._scalar_or(args[2], sheet, seen, depth)
            return False
        if name in ("AND", "OR"):
            if not args:
                return VALUE_ERROR
            # Flattened like a pooled function: Excel's AND/OR accept a range
            # and read every cell, so AND(A1:A3) is TRUE over 1, 2, 3. Reading
            # each argument with ``_eval`` answered #REF! for the range, which
            # made the client engine (which reads the cells) the only one that
            # could use the idiom. An error operand still propagates --
            # AND(1/0) and AND(A1:A5) with an error in A5 are #DIV/0!.
            evaluated: List[Any] = []
            for arg in args:
                expanded, _ = self._expand_arg(arg, sheet, seen, depth)
                evaluated.extend(expanded)
            for value in evaluated:
                if is_error(value):
                    return value
            results = [self._truthy(value) for value in evaluated]
            return all(results) if name == "AND" else any(results)
        if name == "NOT":
            if len(args) != 1:
                return VALUE_ERROR
            value = self._scalar_or(args[0], sheet, seen, depth)
            if is_error(value):
                return value
            return not self._truthy(value)
        if name in ("TRUE", "FALSE"):
            # Excel accepts TRUE()/FALSE() as zero-argument calls alongside
            # the bare literals; the client engine registers them as
            # functions too. The arity is checked before evaluating, the way
            # NOT/IFERROR do, so a stray argument is #VALUE!.
            if args:
                return VALUE_ERROR
            return name == "TRUE"
        if name in ("VLOOKUP", "HLOOKUP"):
            return self._lookup(name, args, sheet, seen, depth)
        if name in ("INDEX", "MATCH", "INDIRECT", "OFFSET", "LOOKUP", "XLOOKUP"):
            # Raw arguments, before the range expansion below: these read
            # their ranges cell by cell, so expanding every range up front
            # propagated an error cell the lookup never touches --
            # INDEX(A1:A5, 1) was #DIV/0! when A5 holds 1/0, where the
            # client engine (and Excel) answer A1. VLOOKUP above is the
            # same shape for the same reason.
            return self._lookup_advanced(name, args, sheet, seen, depth)
        if name in ("SUMIF", "COUNTIF", "AVERAGEIF"):
            return self._criteria(name, args, sheet, seen, depth)
        if name in ("IFERROR", "IFNA"):
            if len(args) != 2:
                return VALUE_ERROR
            # Both slots are single values, so a matrix follows the pooled
            # rule: the error or #VALUE! it raises is what IFERROR catches and
            # IFNA substitutes only for #N/A -- IFERROR(FREQUENCY(A1:A5, A1:A5),
            # 1) is 1, matching the client engine's scalarize.
            error, value = self._scalar_slot(args[0], sheet, seen, depth)
            if error is not None:
                if name == "IFNA" and error != N_A_ERROR:
                    return error
                return self._scalar_or(args[1], sheet, seen, depth)
            return value
        if name in _BRANCHING_FUNCTIONS:
            return self._branched(name, args, sheet, seen, depth)
        if name in _STRUCTURED_FUNCTIONS:
            return self._structured(name, args, sheet, seen, depth)
        if name in (
            "ROUND",
            "ROUNDUP",
            "ROUNDDOWN",
            "MOD",
            "POWER",
            "CEILING",
            "FLOOR",
        ):
            # Scalar slots, read per argument in client order: a coercion
            # failure in an earlier slot beats an error cell in a later
            # range (ROUND(A1:A5, "x") is #VALUE!, not the range's #DIV/0!),
            # which the pooled propagation below could not express -- it
            # scanned the flattened values first and always returned the
            # error cell. ROUND reads its digits first, like the client.
            return self._double_args(name, args, sheet, seen, depth)
        # The numeric families compute in double precision, and the client
        # engine's helpers never raise: an overflow there is an Infinity or a
        # NaN that its result check turns into #NUM!. Python's arithmetic
        # raises instead -- ``math.exp`` overflows, and a denominator whose
        # subtraction rounded away divides by zero -- so the same argument has
        # to land on the same #NUM! here rather than escaping as an exception.
        try:
            if name in _FINANCIAL_FUNCTIONS:
                return self._financial(name, args, sheet, seen, depth)
            if name in _DISTRIBUTION_FUNCTIONS:
                return self._distribution(name, args, sheet, seen, depth)
            if name in _COMBINATORICS_FUNCTIONS:
                return self._combinatorics(name, args, sheet, seen, depth)
        except (OverflowError, ZeroDivisionError, ValueError):
            return NUM_ERROR

        values: List[Any] = []
        # Which entries were read out of a reference, running parallel to
        # ``values``. Excel coerces a text *argument* (SUM("2") is 2) but never
        # a value that came from a reference, so the aggregates need the
        # provenance and not just the flattened values.
        from_reference: List[bool] = []
        if name in _SCALAR_SLOT_FUNCTIONS:
            # A scalar family reads each argument as one slot, the way the
            # client engine's ``scalarize``/``toNumber`` do: a one-cell
            # reference or expression is that value, an error in the slot
            # propagates when the body reads it, and a multi-cell range is
            # #VALUE!. Pooling the range into its cells instead would let an
            # error cell decide the answer before the body read an earlier
            # slot (``BESSELJ("x", A1:A5)`` was the range's #DIV/0! where the
            # client answers the first slot's #VALUE!).
            lead = _SCALAR_SLOT_LEAD.get(name)
            for index, arg in enumerate(args):
                if lead is not None and index >= lead:
                    expanded, is_reference = self._expand_arg(arg, sheet, seen, depth)
                    values.extend(expanded)
                    from_reference.extend([is_reference] * len(expanded))
                    continue
                error, value = self._scalar_slot(arg, sheet, seen, depth)
                values.append(error if error is not None else value)
                from_reference.append(False)
        else:
            for arg in args:
                expanded, is_reference = self._expand_arg(arg, sheet, seen, depth)
                values.extend(expanded)
                from_reference.extend([is_reference] * len(expanded))
        # An error operand propagates out of the value-based functions. Excel
        # treats it as data the caller has to see rather than a value to
        # coerce, and without this the coercion helpers mangled it instead of
        # reporting it: PROPER(1/0) re-cased the error text into "#Div/0!",
        # REPLACE(1/0, 1, 1, "x") concatenated it into "xDIV/0!", and
        # ROUND(1, 1/0) blamed the wrong operand with #VALUE!. COUNT/COUNTA/
        # COUNTBLANK and the *IFS family classify an error themselves (see
        # _ERROR_TOLERANT_FUNCTIONS), and the IS* family answers a question
        # *about* the error rather than failing on it.
        if (
            (name in _VALUE_FUNCTIONS or name in _COMPLEX_FUNCTIONS)
            and name not in _ERROR_TOLERANT_FUNCTIONS
            and name not in _AGGREGATE_ERROR_FUNCTIONS
            and name not in _SCALAR_SLOT_FUNCTIONS
        ):
            for value, from_ref in zip(values, from_reference):
                # COUNTBLANK answers a question about a *cell*, and an error
                # cell is not blank, so one read from a reference is counted
                # rather than propagated. An error written into the formula is
                # not a cell at all and still propagates, which is what the
                # client engine does: COUNTBLANK(A5) is 0 on both sides and
                # COUNTBLANK(1/0) is #DIV/0! on both.
                if is_error(value) and not (name == "COUNTBLANK" and from_ref):
                    return value
        if name in _ADOPTED_STATISTICAL_FUNCTIONS:
            return self._adopted_statistical(name, values, from_reference)
        if name in _ADOPTED_SINGLE_FUNCTIONS:
            return self._adopted_single(name, values)
        if name in _COMPLEX_FUNCTIONS:
            return self._complex(name, values)
        if name in _ENGINEERING_FUNCTIONS:
            return self._engineering(name, values)
        if name in ("SUM", "AVERAGE", "COUNT", "COUNTA", "MIN", "MAX", "PRODUCT"):
            return self._aggregate(name, values, from_reference)
        if name in (
            "ABS",
            "SQRT",
            "INT",
            "LEN",
            "UPPER",
            "LOWER",
            "TRIM",
            "ISBLANK",
            "ISNUMBER",
            "ISTEXT",
        ):
            return self._single(name, values)
        if name in ("ISERROR", "ISNA", "NA", "TYPE"):
            return self._information(name, values)
        if name in ("LEFT", "RIGHT", "MID"):
            return self._text_slice(name, values)
        if name in ("CONCAT", "CONCATENATE"):
            return "".join(_to_text(value) for value in values)
        if name == "EXACT":
            if len(values) != 2:
                return VALUE_ERROR
            return _slot_text(values[0]) == _slot_text(values[1])
        if name == "PROPER":
            if len(values) != 1:
                return VALUE_ERROR
            # Excel capitalises the first letter of every run of letters and
            # lower-cases the rest, so "hello wORLD" is "Hello World".
            # Splitting on whitespace left "o'neil" and "2-cent" untouched
            # after the separator, which Excel capitalises.
            return re.sub(
                r"[a-zA-Z]+",
                lambda match: match.group(0)[:1].upper() + match.group(0)[1:].lower(),
                _to_text(values[0]),
            )
        if name == "SUBSTITUTE":
            return self._substitute(values)
        if name in ("DATE", "YEAR", "MONTH", "DAY"):
            return self._date(name, values)
        if name == "DAYS":
            return self._days(values)
        # ── Advanced functions ──────────────────────────────────────────────
        if name in ("COUNTBLANK", "COUNTIF", "SUMPRODUCT"):
            return self._advanced_aggregate(name, args, sheet, seen, depth)
        if name in (
            "TEXT",
            "VALUE",
            "NUMBERVALUE",
            "FIND",
            "SEARCH",
            "REPLACE",
            "REPT",
            "CHAR",
            "CODE",
        ):
            return self._advanced_text(name, values)
        if name in ("EOMONTH", "EDATE", "WEEKDAY", "WEEKNUM", "NETWORKDAYS", "WORKDAY"):
            if name in ("NETWORKDAYS", "WORKDAY") and len(args) > 3:
                # A holiday range arrives as one argument no matter how many
                # cells it flattens to, so the raw count is what bounds the
                # call: NETWORKDAYS(1, 1, 1, 1) is #VALUE!. Errors still
                # propagate first, through the shared check above.
                return VALUE_ERROR
            return self._advanced_date(name, values)
        if name in (
            "PI",
            "RADIANS",
            "DEGREES",
            "SIN",
            "COS",
            "TAN",
            "ASIN",
            "ACOS",
            "ATAN",
        ):
            return self._advanced_trig(name, values)
        if name in ("AVERAGEIF", "AVERAGEIFS", "SUMIFS", "COUNTIFS"):
            return self._advanced_criteria(name, args, sheet, seen, depth)
        return NAME_ERROR

    def _expand_arg(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> Tuple[List[Any], bool]:
        """Flatten one argument node into values plus a provenance flag.

        The flag is True when the values were read out of a *reference* -- a
        cell, a ``:`` range, or a block returned by OFFSET -- rather than typed
        into the formula. Excel coerces a text argument into a number but never
        a value that came from a reference (``SUM("2")`` is 2 while
        ``SUM(A1)`` is 0 when A1 holds the text "2"), so the aggregates have to
        be able to tell the two apart.
        """
        kind = node[0]
        if kind == "blank":
            # An omitted argument, which ``_expand_arg`` reports as a blank
            # value the way it would a blank cell.
            return [None], False
        if kind == "ref":
            text = node[1]
            if ":" in text:
                first, second = text.split(":", 1)
                s1, r1, c1 = parse_cell_ref(first, self._named_ranges)
                s2, r2, c2 = parse_cell_ref(second, self._named_ranges)
                return (
                    self._range_values(s1 or sheet, r1, c1, r2, c2, seen, depth),
                    True,
                )
            return [self._eval(node, sheet, seen, depth)], True
        value = self._eval(node, sheet, seen, depth)
        if isinstance(value, list):
            # A function returning a block of values (OFFSET) contributes each of
            # them, exactly the way a range argument does. A taller block is a
            # row-major matrix, so a nested row is flattened into the one value
            # list the pooled readers want.
            flattened: List[Any] = []
            for item in value:
                if isinstance(item, list):
                    flattened.extend(item)
                else:
                    flattened.append(item)
            return flattened, True
        return [value], False

    def _argument_error(
        self,
        args: List[Any],
        sheet: Optional[str],
        seen: set,
        depth: int,
        defer_references: bool = False,
    ) -> Optional[str]:
        """The error a *value argument* raises before the body reads it.

        The client engine evaluates every argument before it calls the
        function, so an argument that evaluates to an error propagates in
        argument order -- ahead of an error cell inside a later range read or
        a length mismatch. A multi-cell range is not such a value: it
        evaluates to a matrix the function reads for itself, so only a single
        value -- a literal, a single-cell reference or an expression -- is
        checked here.

        ``defer_references`` is the flat range reader's rule: it hands over
        *every* reference unevaluated -- a single cell as much as a ``:``
        range -- so both are skipped here and only an expression or a literal
        argument is inspected.
        """
        for arg in args:
            if defer_references:
                if arg[0] == "ref":
                    continue
            elif arg[0] == "ref" and ":" in arg[1]:
                continue
            value = self._eval(arg, sheet, seen, depth)
            if is_error(value):
                return value
        return None
