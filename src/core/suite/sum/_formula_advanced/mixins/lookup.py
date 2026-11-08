"""Formula evaluator mixin: lookup and reference functions."""

import math
from typing import Any, List, Optional
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    ERROR_CODES,
    MAX_BLOCK_CELLS,
    NUM_ERROR,
    N_A_ERROR,
    REF_ERROR,
    VALUE_ERROR,
    _compare_values,
    _computed_range_error,
    _to_number,
    _to_text,
    is_error,
    parse_cell_ref,
)
from ..helpers.numbers import _lookup_vector


class LookupMixin(AdvancedEvaluatorBase):
    def _lookup_advanced(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """INDEX, MATCH, INDIRECT, OFFSET, LOOKUP, XLOOKUP."""
        if name == "INDEX":
            # INDEX(array, row_num, [col_num])
            if len(args) < 2 or len(args) > 3:
                return VALUE_ERROR
            # A single cell is a 1x1 block, and a nested call (OFFSET) that
            # resolved to a block of values is a one-row block. Both used to
            # reach _range_matrix, which only describes "A1:B2" refs, so a
            # formula naming one cell came back #REF!.
            matrix = self._criteria_range(args[0], sheet, seen, depth)
            if not matrix:
                value = self._eval(args[0], sheet, seen, depth)
                if is_error(value):
                    return value
                if isinstance(value, list) and value and isinstance(value[0], list):
                    # A block from OFFSET arrives row-major already.
                    matrix = value
                elif isinstance(value, list):
                    matrix = [list(value)]
                else:
                    matrix = [[value]]
            # Excel truncates a fractional offset, rejects a negative one with
            # #VALUE!, and reads a 0 as "every row" / "every column" -- so
            # INDEX(A1:C3, 0, 2) is the whole second column, which SUM can add
            # up, rather than the #REF! this used to answer.
            row_value = self._eval(args[1], sheet, seen, depth)
            if is_error(row_value):
                return row_value
            row_num = int(_to_number(row_value))
            col_num = 1
            if len(args) == 3:
                col_value = self._eval(args[2], sheet, seen, depth)
                if is_error(col_value):
                    return col_value
                col_num = int(_to_number(col_value))
            if row_num < 0 or col_num < 0:
                return VALUE_ERROR
            width = max((len(row) for row in matrix), default=0)
            if row_num > len(matrix) or col_num > width:
                return REF_ERROR
            rows = matrix if row_num == 0 else [matrix[row_num - 1]]
            block = [row if col_num == 0 else [row[col_num - 1]] for row in rows]
            # Shape the block the way the client engine's INDEX does: a 1x1
            # block is the value, a single row is a flat vector, and anything
            # else stays the row-major matrix (a whole column is a column of
            # one-element rows). Collapsing every block to one flat list lost
            # the orientation OFFSET already keeps, so XLOOKUP(1, 1,
            # INDEX(A1:A5, 0, 1)) saw a one-row block where the client sees a
            # five-row column and answered an array instead of #VALUE!.
            if len(block) == 1:
                row_values = list(block[0])
                return row_values[0] if len(row_values) == 1 else row_values
            return [list(row) for row in block]
        elif name == "MATCH":
            # MATCH(lookup_value, lookup_array, [match_type])
            if len(args) < 2 or len(args) > 3:
                return VALUE_ERROR
            # The match type is coerced first, the way the client engine reads
            # it before it searches: MATCH(A1:A5, 1, "x") is the #VALUE! from
            # the match type, not the lookup range's error cell.
            match_type = 1
            if len(args) == 3:
                type_value = self._eval(args[2], sheet, seen, depth)
                if is_error(type_value):
                    return type_value
                try:
                    match_type = int(_to_number(type_value))
                except ValueError as exc:
                    message = str(exc)
                    return message if message in ERROR_CODES else VALUE_ERROR
            lookup_value = self._lookup_value(args[0], sheet, seen, depth)
            matrix = self._range_matrix(args[1], sheet, seen, depth)
            if not matrix:
                return N_A_ERROR
            # An error written into the lookup range propagates; an error
            # cell only fails to match (see _computed_range_error).
            range_error = _computed_range_error(args[1], matrix)
            if range_error is not None:
                return range_error
            # Flatten matrix for matching, a one-row matrix as a horizontal
            # vector (see _lookup_vector).
            flat = _lookup_vector(matrix)
            if match_type == 0:  # Exact match
                # A wildcard match is what Excel calls an exact match here:
                # MATCH("ap*", D1:D3, 0) finds "apple".
                for i, val in enumerate(flat):
                    if self._lookup_eq(val, lookup_value, True):
                        return float(i + 1)
            elif match_type == 1:  # Less than (sorted ascending)
                # Sorted ascending, so the answer is the last entry <= the
                # lookup value. Entries are ranked the way Excel orders types
                # rather than coerced with _to_number, which raised ValueError
                # on the first text entry and left the scan reporting the
                # length of the vector for every text lookup.
                for i, val in enumerate(flat):
                    if _compare_values(val, lookup_value) > 0:
                        return float(i) if i > 0 else N_A_ERROR
                return float(len(flat))
            elif match_type == -1:  # Greater than (sorted descending)
                for i, val in enumerate(flat):
                    if _compare_values(val, lookup_value) < 0:
                        return float(i) if i > 0 else N_A_ERROR
                return float(len(flat))
            return N_A_ERROR
        elif name == "INDIRECT":
            # INDIRECT(ref_text, [a1])
            if len(args) < 1 or len(args) > 2:
                return VALUE_ERROR
            # The ref_text argument is one value, so a matrix -- a range or a
            # nested call -- follows the pooled rule (the client's scalarize):
            # INDIRECT(FREQUENCY(A1:A5, A1:A5)) is #VALUE! and
            # INDIRECT(OFFSET(A1, 0, 0, 5, 1)) propagates the error cell.
            ref_value = self._scalar_or(args[0], sheet, seen, depth)
            if is_error(ref_value):
                return ref_value
            ref_text = _to_text(ref_value)
            try:
                r_sheet, row, col = parse_cell_ref(ref_text, self._named_ranges)
                target = r_sheet or sheet
                return self._resolve(target, row, col, seen, depth)
            except ValueError:
                return REF_ERROR
        elif name == "OFFSET":
            # OFFSET(reference, rows, cols, [height], [width])
            if len(args) < 3 or len(args) > 5:
                return VALUE_ERROR
            # Get the reference range
            ref_node = args[0]
            if ref_node[0] != "ref":
                return VALUE_ERROR
            ref_text = ref_node[1]
            if ":" not in ref_text:
                # Single cell reference
                first = ref_text
                second = ref_text
            else:
                first, second = ref_text.split(":", 1)
            try:
                s1, r1, c1 = parse_cell_ref(first, self._named_ranges)
                s2, r2, c2 = parse_cell_ref(second, self._named_ranges)
            except ValueError:
                return REF_ERROR
            target = s1 or sheet
            # The rows offset is coerced before the cols *argument* is even
            # read: the client engine's OFFSET reads its numbers in order, so a
            # non-numeric rows (``"x"``) is #VALUE! ahead of an error cell or
            # error operand in cols (``OFFSET(A1, "x", 1/0)``).
            rows_value = self._eval(args[1], sheet, seen, depth)
            if is_error(rows_value):
                return rows_value
            rows = int(_to_number(rows_value))
            cols_value = self._eval(args[2], sheet, seen, depth)
            if is_error(cols_value):
                return cols_value
            cols = int(_to_number(cols_value))
            height = r2 - r1 + 1
            width = c2 - c1 + 1
            if len(args) >= 4:
                height_value = self._eval(args[3], sheet, seen, depth)
                if is_error(height_value):
                    return height_value
                height = _to_number(height_value)
            if len(args) == 5:
                width_value = self._eval(args[4], sheet, seen, depth)
                if is_error(width_value):
                    return width_value
                width = _to_number(width_value)
            if not (height >= 1) or not (width >= 1):
                # Excel needs a positive height and width; zero or negative
                # spans are #REF!, not an empty block leaking out as a value.
                # A fractional 0.5 fails here too, as the client engine's
                # ``!(height >= 1)`` does.
                return REF_ERROR
            # The span is a loop bound, so a fractional 1.9 reaches two cells
            # (``c < col + width`` in the client engine), not one.
            height = math.ceil(height)
            width = math.ceil(width)
            # Bound the materialised block before the loops run: the spans are
            # unrestricted doubles, so OFFSET(A1,0,0,1e9,1e9) used to build a
            # 10^18-cell block and stall the thread.
            if height * width > MAX_BLOCK_CELLS:
                return NUM_ERROR
            block = []
            for r in range(r1 + rows, r1 + rows + height):
                row_values = []
                for c in range(c1 + cols, c1 + cols + width):
                    row_values.append(self._resolve(target, r, c, seen, depth + 1))
                block.append(row_values)
            # A single row collapses to a flat row and a single cell to its
            # value; a taller block stays a row-major matrix -- the shape the
            # client engine's ``collapseBlock`` returns and the block readers
            # (INDEX, the argument flattener) already expect.
            if len(block) == 1:
                row_values = block[0]
                return row_values[0] if len(row_values) == 1 else row_values
            return block
        elif name == "LOOKUP":
            # LOOKUP(lookup_value, lookup_vector, [result_vector])
            if len(args) < 2 or len(args) > 3:
                return VALUE_ERROR
            lookup_value = self._lookup_value(args[0], sheet, seen, depth)
            matrix = self._range_matrix(args[1], sheet, seen, depth)
            if not matrix:
                return N_A_ERROR
            range_error = _computed_range_error(args[1], matrix)
            if range_error is not None:
                return range_error
            flat = _lookup_vector(matrix)
            result_flat = flat
            if len(args) == 3:
                result_matrix = self._range_matrix(args[2], sheet, seen, depth)
                result_error = _computed_range_error(args[2], result_matrix)
                if result_error is not None:
                    return result_error
                result_flat = _lookup_vector(result_matrix)
            # LOOKUP assumes sorted ascending, finds last value <= lookup_value.
            # The comparison ranks types the way Excel orders them, because
            # _to_number raised ValueError on the first text entry and the scan
            # then reported #N/A for a vector of text entirely.
            best_idx = -1
            for i, val in enumerate(flat):
                if _compare_values(val, lookup_value) <= 0:
                    best_idx = i
                else:
                    # Sorted ascending: once the vector passes the lookup value
                    # every later entry does too, so the scan can stop.
                    break
            if best_idx >= 0 and best_idx < len(result_flat):
                return result_flat[best_idx]
            return N_A_ERROR
        elif name == "XLOOKUP":
            # XLOOKUP(lookup_value, lookup_array, return_array, [if_not_found],
            #         [match_mode], [search_mode])
            # match_mode: 0 exact (default), -1 exact or next smaller,
            #             1 exact or next larger, 2 wildcard (*, ?, ~).
            # search_mode: 1 first-to-last (default), -1 last-to-first,
            #              2 binary ascending, -2 binary descending.
            # The binary modes scan linearly in the same direction here: on
            # sorted data the answers agree, and this engine never promises
            # logarithmic time, only the same value.
            if len(args) < 3 or len(args) > 6:
                return VALUE_ERROR
            # A multi-cell lookup *value* is read as the matrix the client
            # engine keeps -- it compares equal to nothing -- rather than
            # collapsed to its error cell by ``_eval``, which answered #DIV/0!
            # for XLOOKUP(A1:A5, 1, NA()) where the client answers #N/A. A
            # scalar error still propagates.
            lookup_value = self._lookup_value(args[0], sheet, seen, depth)
            if is_error(lookup_value):
                return lookup_value
            lookup_matrix = self._range_matrix(args[1], sheet, seen, depth)
            if not lookup_matrix:
                value = self._eval(args[1], sheet, seen, depth)
                if is_error(value):
                    return value
                lookup_matrix = [list(value)] if isinstance(value, list) else [[value]]
            lookup_error = _computed_range_error(args[1], lookup_matrix)
            if lookup_error is not None:
                return lookup_error
            return_matrix = self._range_matrix(args[2], sheet, seen, depth)
            if not return_matrix:
                value = self._eval(args[2], sheet, seen, depth)
                if is_error(value):
                    return value
                return_matrix = [list(value)] if isinstance(value, list) else [[value]]
            return_error = _computed_range_error(args[2], return_matrix)
            if return_error is not None:
                return return_error
            match_mode = 0
            if len(args) >= 5:
                mode_value = self._eval(args[4], sheet, seen, depth)
                if is_error(mode_value):
                    return mode_value
                try:
                    match_mode = int(_to_number(mode_value))
                except ValueError as exc:
                    return str(exc)
            search_mode = 1
            if len(args) == 6:
                search_value = self._eval(args[5], sheet, seen, depth)
                if is_error(search_value):
                    return search_value
                try:
                    search_mode = int(_to_number(search_value))
                except ValueError as exc:
                    return str(exc)
            if match_mode not in (0, -1, 1, 2):
                return VALUE_ERROR
            if search_mode not in (1, -1, 2, -2):
                return VALUE_ERROR
            # A single row is a horizontal vector; anything taller is
            # searched by its first column, the way VLOOKUP keys on the
            # first column of its table.
            lookup_vector = _lookup_vector(lookup_matrix)
            # The return block answers row-wise when it has one row per
            # lookup entry, and column-wise when both blocks are a single
            # row of the same length (the horizontal form).
            column_wise = False
            if len(return_matrix) == len(lookup_vector):
                return_rows = [list(row) for row in return_matrix]
            elif (
                len(lookup_matrix) == 1
                and len(return_matrix) == 1
                and len(return_matrix[0]) == len(lookup_vector)
            ):
                column_wise = True
                return_rows = [list(return_matrix[0])]
            else:
                return VALUE_ERROR
            # Once the arrays and their shapes are settled, a multi-cell lookup
            # *value* surfaces its error cell in the comparison -- the client
            # engine's scalarize raises it while matching, so
            # XLOOKUP(A1:A5, 1, 1) is the lookup value's #DIV/0!.
            if isinstance(lookup_value, list):
                for item in lookup_value:
                    if is_error(item):
                        return item
                # A plain multi-cell value with no error cell compares equal to
                # nothing, and the client engine's scalarize answers #VALUE! for
                # it: XLOOKUP(A1:A3, 1, 1) is #VALUE! rather than #N/A.
                return VALUE_ERROR
            if search_mode in (-1, -2):
                order = list(range(len(lookup_vector) - 1, -1, -1))
            else:
                order = list(range(len(lookup_vector)))
            matched = -1
            # The approximate modes answer the *closest* value on the right
            # side -- the smallest entry ranked at or above the lookup for
            # mode 1, the largest at or below it for mode -1 -- rather than
            # the first entry the scan crosses. A forward scan that stopped
            # at the first entry <= the lookup answered 1 for
            # XLOOKUP(2.5, {1,2,3}, ..., -1) where the next smaller is 2.
            # Ties keep the first entry in search order.
            best_probe: Any = None
            for i in order:
                probe = lookup_vector[i]
                if match_mode == 0:
                    if self._lookup_eq(probe, lookup_value):
                        matched = i
                        break
                elif match_mode == 2:
                    if self._lookup_eq(probe, lookup_value, True):
                        matched = i
                        break
                elif match_mode == 1:
                    # Exact or next larger: the first entry that ranks at
                    # or above the lookup value. Ranked comparison (not
                    # numeric coercion) is what lets text order too.
                    if _compare_values(probe, lookup_value) >= 0 and (
                        matched < 0 or _compare_values(probe, best_probe) < 0
                    ):
                        matched = i
                        best_probe = probe
                else:
                    if _compare_values(probe, lookup_value) <= 0 and (
                        matched < 0 or _compare_values(probe, best_probe) > 0
                    ):
                        matched = i
                        best_probe = probe
            if matched < 0:
                # if_not_found evaluates only on a miss, so a costly or
                # failing fallback never poisons a hit.
                if len(args) >= 4:
                    return self._eval(args[3], sheet, seen, depth)
                return N_A_ERROR
            if column_wise:
                return return_rows[0][matched]
            row = return_rows[matched]
            if len(row) == 1:
                return row[0]
            return list(row)
        return VALUE_ERROR
