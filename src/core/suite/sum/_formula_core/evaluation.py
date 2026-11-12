"""Cell, reference, range, and operator evaluation."""

import math
from typing import Any, List, Optional

from .._formula_shared import (
    CYCLE_ERROR,
    DIV_ZERO,
    ERROR_CODES,
    MAX_CALL_DEPTH,
    NUM_ERROR,
    REF_ERROR,
    VALUE_ERROR,
    _scalarize,
    _text_equals,
    _to_number,
    _to_text,
    is_error,
    parse_cell_ref,
    parse_formula,
)
from .base import FormulaEvaluatorCoreBase, _DependencyBudgetExceeded


class EvaluationMixin(FormulaEvaluatorCoreBase):
    """Evaluate parsed ASTs against the caller's cell resolver."""

    def evaluate_cell(
        self,
        sheet: Optional[str],
        row: int,
        column: int,
        formula: str,
        seen: Optional[set] = None,
        depth: int = 0,
    ) -> Any:
        key = (sheet, row, column, formula)
        if depth == 0:
            # Root evaluation starts a fresh dependency budget. Without this
            # the counter would be cumulative across every formula cell in a
            # viewport, so a dense sheet of many multi-reference formulas
            # could spuriously exceed the bound and return #NUM!.
            self._used = 0
        if depth > MAX_CALL_DEPTH:
            return CYCLE_ERROR
        if seen is None:
            seen = set()
        if key in seen:
            return CYCLE_ERROR
        if key in self._computed:
            return self._computed[key]
        self._used += 1
        if self._used > self._dependencies:
            raise _DependencyBudgetExceeded
        seen.add(key)
        try:
            node = parse_formula(formula)
            if node[0] == "ref" and ":" in node[1]:
                # A bare range is the *matrix* the client engine's top-level
                # ``evaluate`` returns (and a formula cell holding one spills
                # the same way). Reading it through ``_eval`` applied the
                # scalar-slot pooled rule instead, so ``=A1:A5`` was its error
                # cell (#DIV/0!) rather than the five-cell column.
                value = self._range_matrix(node, sheet, seen, depth)
            else:
                value = self._eval(node, sheet, seen, depth)
            self._computed[key] = value
            return value
        except _DependencyBudgetExceeded:
            return NUM_ERROR
        except ValueError as exc:
            message = str(exc)
            return message if message in ERROR_CODES else VALUE_ERROR
        finally:
            seen.discard(key)

    def _resolve(
        self, sheet: Optional[str], row: int, column: int, seen: set, depth: int
    ) -> Any:
        cell = self._resolver(sheet, row, column)
        if cell is None:
            return None
        self._used += 1
        if self._used > self._dependencies:
            raise _DependencyBudgetExceeded
        formula = cell.get("formula")
        if formula:
            return self.evaluate_cell(sheet, row, column, formula, seen, depth + 1)
        return cell.get("value")

    def _range_values(
        self,
        sheet: Optional[str],
        row1: int,
        col1: int,
        row2: int,
        col2: int,
        seen: set,
        depth: int,
    ) -> List[Any]:
        values: List[Any] = []
        r_start, r_end = sorted((row1, row2))
        c_start, c_end = sorted((col1, col2))
        for r in range(r_start, r_end + 1):
            for c in range(c_start, c_end + 1):
                self._used += 1
                if self._used > self._dependencies:
                    raise _DependencyBudgetExceeded
                values.append(self._resolve(sheet, r, c, seen, depth + 1))
        return values

    def _eval(self, node: Any, sheet: Optional[str], seen: set, depth: int) -> Any:
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "str":
            return node[1]
        if kind == "bool":
            return node[1]
        if kind == "blank":
            # An omitted argument is Excel's "missing" operand, which behaves
            # exactly like a blank cell (0 in arithmetic, "" as text).
            return None
        if kind == "ref":
            ref = node[1]
            if ":" in ref:
                # A range reached a slot that wants one value (an operator, an
                # IF condition, a lookup key). It used to fall through
                # ``parse_cell_ref`` and answer #REF! for the colon; the
                # client engine read it as a row-major matrix. Both now apply
                # the pooled rule the scalar functions share: an error cell in
                # the range propagates, a one-cell range is that cell, and
                # anything else is #VALUE! -- never a comma-joined string.
                first, second = ref.split(":", 1)
                try:
                    s1, r1, c1 = parse_cell_ref(first, self._named_ranges)
                    s2, r2, c2 = parse_cell_ref(second, self._named_ranges)
                except ValueError:
                    return REF_ERROR
                values = self._range_values(s1 or sheet, r1, c1, r2, c2, seen, depth)
                for value in values:
                    if is_error(value):
                        return value
                if len(values) == 1:
                    return values[0]
                return VALUE_ERROR
            try:
                r_sheet, row, column = parse_cell_ref(ref, self._named_ranges)
            except ValueError:
                # A name that is neither a cell reference nor a defined named
                # range is #REF!, the answer the client engine gives. It used
                # to reach evaluate_cell's catch-all and surface as #VALUE!.
                return REF_ERROR
            target_sheet = r_sheet or sheet
            return self._resolve(target_sheet, row, column, seen, depth)
        if kind == "neg":
            value = self._eval(node[1], sheet, seen, depth)
            if is_error(value):
                return value
            # A coercion failure is returned as an error *value*, like every
            # other branch: raising past an enclosing error-catching function
            # (``IFERROR(-"abc", 9)``, ``ISERROR(K1%)``) lost the failure,
            # where the client engine's unary evaluator yields the value its
            # body failed with.
            try:
                return -_to_number(value)
            except ValueError as exc:
                return str(exc)
        if kind == "%":
            value = self._eval(node[1], sheet, seen, depth)
            if is_error(value):
                return value
            try:
                return _to_number(value) / 100.0
            except ValueError as exc:
                return str(exc)
        if kind == "cmp":
            # Comparison nodes are ("cmp", op, left, right).
            op = node[1]
            left = self._operand(node[2], sheet, seen, depth)
            # Left-first, and the right operand is not evaluated once the left
            # has failed. The right operand may *raise* (a nested coercion
            # failure such as ``A5 + -"abc"`` or ``NA() + A1:A5/1``), and an
            # uncaught raise would outrank the left error -- the same ordering
            # the client engine's evaluateBinaryOp fixes by short-circuiting.
            if is_error(left):
                return left
            right = self._operand(node[3], sheet, seen, depth)
            return self._apply(op, left, right)
        if kind in ("+", "-", "*", "/", "^", "&"):
            op = kind
            left = self._operand(node[1], sheet, seen, depth)
            if is_error(left):
                return left
            right = self._operand(node[2], sheet, seen, depth)
            return self._apply(op, left, right)
        if kind == "call":
            name = node[1]
            args = node[2]
            return self._call(name, args, sheet, seen, depth)
        raise ValueError(VALUE_ERROR)

    def _operand(self, node: Any, sheet: Optional[str], seen: set, depth: int) -> Any:
        """Evaluate one operand of a binary operator.

        A range operand keeps its matrix shape -- the client engine evaluates
        a range reference to an array and only reduces it when the operator
        applies it. Reducing it here instead (the scalar-slot rule ``_eval``
        applies to a bare range) let the range's own #VALUE!/error cell mask
        the *other* operand's error, so ``A1:A3 + A5`` was #VALUE! rather than
        A5's #DIV/0!.
        """
        if node[0] == "ref" and ":" in node[1]:
            return self._range_matrix(node, sheet, seen, depth)
        return self._eval(node, sheet, seen, depth)

    def _apply(self, op: str, left: Any, right: Any) -> Any:
        if is_error(left):
            return left
        if is_error(right):
            return right
        if op == "&":
            try:
                return _to_text(_scalarize(left)) + _to_text(_scalarize(right))
            except ValueError as exc:
                return str(exc)
        if op in ("=", "<>", "<", ">", "<=", ">="):
            if op in ("=", "<>"):
                try:
                    left = _scalarize(left)
                    right = _scalarize(right)
                except ValueError as exc:
                    return str(exc)
                if isinstance(left, str) or isinstance(right, str):
                    # Excel's text comparison ignores case; EXACT is the
                    # case-sensitive test. A plain ``==`` here answered FALSE
                    # for "a" = "A".
                    comparison = _text_equals(left, right)
                elif isinstance(left, bool) != isinstance(right, bool):
                    # Excel ranks types before comparing, so a logical is never
                    # its numeric value: TRUE = 1 is FALSE and TRUE > 1 is
                    # TRUE. Left to Python, ``True == 1`` answered TRUE, which
                    # disagreed with the client engine (and with the ranked
                    # comparisons the criteria functions already use).
                    comparison = False
                else:
                    comparison = left == right
                return comparison if op == "=" else not comparison
            try:
                ln = _to_number(left)
                rn = _to_number(right)
            except ValueError as exc:
                return str(exc)
            if op == "<":
                return ln < rn
            if op == ">":
                return ln > rn
            if op == "<=":
                return ln <= rn
            return ln >= rn
        if op == "+":
            # Text that reads as a number is coerced ("2"+3 is 5) and anything
            # else is #VALUE!. The old guard rejected *every* text operand, so a
            # numeric string was refused even though "2"*3 and "2"/2 both
            # coerced it.
            try:
                return _to_number(left) + _to_number(right)
            except ValueError as exc:
                return str(exc)
        if op == "-":
            try:
                return _to_number(left) - _to_number(right)
            except ValueError as exc:
                return str(exc)
        if op == "*":
            try:
                return _to_number(left) * _to_number(right)
            except ValueError as exc:
                return str(exc)
        if op == "/":
            # The divisor is read first, as the client engine does: a zero
            # divisor is #DIV/0! before the dividend is coerced. The dividend
            # coercion is guarded too, so a nested left failure is returned as
            # an error value instead of raising past an enclosing operator.
            try:
                rn = _to_number(right)
            except ValueError as exc:
                return str(exc)
            if rn == 0:
                return DIV_ZERO
            try:
                ln = _to_number(left)
            except ValueError as exc:
                return str(exc)
            return ln / rn
        if op == "^":
            try:
                base = _to_number(left)
                exponent = _to_number(right)
            except ValueError as exc:
                return str(exc)
            if base == 0 and exponent < 0:
                # 0 ** -n is 1 / 0**n, a division by zero in Excel.
                return DIV_ZERO
            try:
                return math.pow(base, exponent)
            except (ValueError, OverflowError):
                return NUM_ERROR
        return VALUE_ERROR
