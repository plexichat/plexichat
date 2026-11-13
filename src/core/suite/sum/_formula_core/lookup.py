"""Range matrices and the lookup/reference functions."""

from typing import Any, List, Optional

from .._formula_shared import (
    ERROR_CODES,
    N_A_ERROR,
    NUM_ERROR,
    REF_ERROR,
    VALUE_ERROR,
    _computed_range_error,
    _is_pattern,
    _scalarize,
    _text_equals,
    _to_number,
    _wildcard_match,
    parse_cell_ref,
)
from .base import FormulaEvaluatorCoreBase


class LookupFunctionsMixin(FormulaEvaluatorCoreBase):
    """V/HLOOKUP, lookup values, and shape-preserving range reads."""

    def _range_matrix(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> List[List[Any]]:
        """Resolve a ``A1:C10`` ref node into a 2-D matrix of values.

        A single-cell reference (``B6``) is Excel's 1x1 array rather than an
        empty range, so MATCH, LOOKUP, V/HLOOKUP and SUMPRODUCT read that one
        cell instead of answering #VALUE!/#N/A for a one-cell argument.

        Lookup and criteria functions need the *shape* of a range (rows ×
        columns), which the flat ``_expand_arg`` path discards.

        Anything that is not a ``ref`` node -- a literal, or a nested call
        such as OFFSET/INDEX -- is read the way the client engine reads it: a
        single value is a 1x1 array and a block of values is one row, never an
        empty range. Returning ``[]`` for a literal made every range function
        see no cells at all, so ``COUNTIF("abc", "abc")`` was 0 and
        ``MATCH("a", "a", 0)`` was #N/A where the client answered 1.
        """
        if node[0] != "ref":
            value = self._eval(node, sheet, seen, depth)
            if isinstance(value, list):
                return (
                    [list(row) for row in value]
                    if (value and isinstance(value[0], list))
                    else [list(value)]
                )
            return [[value]]
        if ":" in node[1]:
            first, second = node[1].split(":", 1)
        else:
            first = second = node[1]
        s1, r1, c1 = parse_cell_ref(first, self._named_ranges)
        s2, r2, c2 = parse_cell_ref(second, self._named_ranges)
        target = s1 or sheet
        r_start, r_end = sorted((r1, r2))
        c_start, c_end = sorted((c1, c2))
        matrix: List[List[Any]] = []
        for r in range(r_start, r_end + 1):
            row_values: List[Any] = []
            for c in range(c_start, c_end + 1):
                self._used += 1
                if self._used > self._dependencies:
                    raise ValueError(NUM_ERROR)
                row_values.append(self._resolve(target, r, c, seen, depth + 1))
            matrix.append(row_values)
        return matrix

    def _criteria_range(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> List[List[Any]]:
        """Resolve a criteria/sum range.

        A single cell is a 1x1 range to these functions (``COUNTIF(B6, x)``),
        which ``_range_matrix`` describes.
        """
        return self._range_matrix(node, sheet, seen, depth)

    @staticmethod
    def _lookup_eq(a: Any, b: Any, wildcards: bool = False) -> bool:
        """Equality used by V/HLOOKUP: numeric cells compare numerically,
        anything text-like compares as text.

        ``wildcards`` adds Excel's exact-lookup pattern rule: a *text* key that
        reads as a pattern (``_is_pattern``) matches text cells instead of
        comparing literally, so ``MATCH("ap*", D1:D3, 0)`` finds ``apple`` and
        ``MATCH("tw~*", L1, 0)`` finds the literal ``tw*``. Only text can
        match, the same rule the criteria comparisons use, and the approximate
        forms never pattern-match, so they leave the flag off.
        """
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        if isinstance(b, (list, tuple)):
            # The key is one value, the way the client engine's ``lookupEquals``
            # reads it: its ``toNumber``/``toText`` scalarize the lookup value,
            # so an error cell inside a multi-cell key propagates
            # (MATCH(A1:A5, A1:A5, 0) is #DIV/0!) and a multi-cell key with no
            # error is #VALUE! (MATCH(A1:A2, A1:A5, 0)). The approximate forms
            # never reach here -- they rank the key with ``_compare_values`` --
            # so only the exact comparison scalarizes, exactly as the client's
            # exact branch does and its approximate branch does not.
            b = _scalarize(b)
        if wildcards and isinstance(b, str) and _is_pattern(b):
            return isinstance(a, str) and _wildcard_match(b, a)
        if isinstance(a, str) or isinstance(b, str):
            return _text_equals(a, b)
        if isinstance(a, bool) != isinstance(b, bool):
            # A logical never matches a number, for the same ranking reason
            # ``=`` does not: MATCH(1, {TRUE}, 0) is #N/A, not 1.
            return False
        try:
            return _to_number(a) == _to_number(b)
        except ValueError:
            return _text_equals(a, b)

    def _lookup_value(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """Read a lookup value the way the client engine keeps it.

        A single value -- a literal, an expression or a one-cell reference --
        is that value; a multi-cell range arrives as the *matrix* the client
        keeps, so a key that "compares equal to nothing" leaves the body to
        report its own error (MATCH's approximate branches answer #N/A,
        LOOKUP/XLOOKUP answer #VALUE!). The *exact* comparison is the
        exception: it scalarizes the key in ``_lookup_eq``, exactly as the
        client's ``lookupEquals`` does, so an error cell in a multi-cell key
        propagates and a multi-cell key with no error is #VALUE!.
        """
        values, _ = self._expand_arg(node, sheet, seen, depth)
        if len(values) == 1:
            return values[0]
        return list(values)

    def _lookup(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """VLOOKUP / HLOOKUP with exact or sorted approximate matching."""
        if len(args) < 3 or len(args) > 4:
            return VALUE_ERROR
        # The column/row index is coerced before the lookup value or the table
        # is read, the way the client engine orders it: VLOOKUP(A1:A5, 1, "x")
        # is the #VALUE! from the index, not the lookup range's error cell.
        try:
            index = int(_to_number(self._eval(args[2], sheet, seen, depth))) - 1
        except ValueError as exc:
            message = str(exc)
            return message if message in ERROR_CODES else VALUE_ERROR
        if index < 0:
            return VALUE_ERROR
        # The lookup value is read as one *slot*, the way the client engine
        # scalarizes it: an error cell in a multi-cell value propagates and a
        # multi-cell value with no error is #VALUE! (VLOOKUP(A1:A3, 1, 1)).
        # Keeping it a matrix that "compares equal to nothing" answered #N/A
        # where the client propagates the error or reports #VALUE!.
        lookup_error, lookup_scalar = self._scalar_slot(args[0], sheet, seen, depth)
        if lookup_error is not None:
            return lookup_error
        lookup_value = lookup_scalar
        matrix = self._range_matrix(args[1], sheet, seen, depth)
        if not matrix:
            return N_A_ERROR
        # An error written into the table propagates instead of reading as
        # a row that never matches (see _computed_range_error): VLOOKUP over
        # INDIRECT("A1:B5") is #REF!, not #N/A.
        table_error = _computed_range_error(args[1], matrix)
        if table_error is not None:
            return table_error
        range_lookup = True
        if len(args) == 4:
            range_lookup = bool(self._truthy(self._eval(args[3], sheet, seen, depth)))

        if name == "HLOOKUP":
            if not matrix[0]:
                return N_A_ERROR
            if index >= len(matrix):
                return REF_ERROR
            key_row = matrix[0]
            for c, key in enumerate(key_row):
                if self._lookup_eq(key, lookup_value, not range_lookup):
                    return matrix[index][c]
            if range_lookup:
                try:
                    target = _to_number(lookup_value)
                except ValueError:
                    return N_A_ERROR
                best = None
                for c, key in enumerate(key_row):
                    if key is None or key == "":
                        continue
                    try:
                        if _to_number(key) <= target:
                            best = matrix[index][c]
                        else:
                            break
                    except ValueError:
                        continue
                return best if best is not None else N_A_ERROR
            return N_A_ERROR

        # VLOOKUP: search the first column.
        if index >= len(matrix[0]):
            return REF_ERROR
        for row in matrix:
            if not row:
                continue
            if self._lookup_eq(row[0], lookup_value, not range_lookup):
                return row[index]
        if range_lookup:
            try:
                target = _to_number(lookup_value)
            except ValueError:
                return N_A_ERROR
            best = None
            for row in matrix:
                if not row:
                    continue
                key = row[0]
                if key is None or key == "":
                    continue
                try:
                    if _to_number(key) <= target:
                        best = row[index]
                    else:
                        break
                except ValueError:
                    continue
            return best if best is not None else N_A_ERROR
        return N_A_ERROR
