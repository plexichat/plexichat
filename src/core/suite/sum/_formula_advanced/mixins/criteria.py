"""Formula evaluator mixin: criteria-based functions."""

from typing import Any, List, Optional
from ..base import AdvancedEvaluatorBase
from ..._formula_shared import (
    DIV_ZERO,
    VALUE_ERROR,
    _computed_range_error,
    _numeric,
    _to_number_or_zero,
    is_error,
)


class CriteriaMixin(AdvancedEvaluatorBase):
    def _advanced_criteria(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        """AVERAGEIF, AVERAGEIFS, SUMIFS, COUNTIFS with multi-criteria support."""
        if name == "AVERAGEIF":
            return self._criteria("AVERAGEIF", args, sheet, seen, depth)
        if name == "SUMIFS":
            if len(args) < 3 or len(args) % 2 != 1:
                return VALUE_ERROR
            sum_range_node = args[0]
            sum_matrix = self._criteria_range(sum_range_node, sheet, seen, depth)
            if not sum_matrix:
                return 0.0
            # An error written into the sum range propagates (an error cell
            # is read as 0 when its row matches, as below).
            sum_error = _computed_range_error(sum_range_node, sum_matrix)
            if sum_error is not None:
                return sum_error
            num_criteria = (len(args) - 1) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_node = args[1 + i * 2]
                cr_matrix = self._criteria_range(cr_node, sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                range_error = _computed_range_error(cr_node, cr_matrix)
                if range_error is not None:
                    return range_error
                crit_node = args[2 + i * 2]
                if crit_node[0] == "str":
                    criteria_list.append(crit_node[1])
                elif crit_node[0] == "num":
                    criteria_list.append(crit_node[1])
                else:
                    # A criteria argument is one value: scalarize a matrix the
                    # way the client engine does.
                    criteria = self._scalar_or(crit_node, sheet, seen, depth)
                    if is_error(criteria):
                        # An error criteria operand propagates; an error cell
                        # inside a criteria range only fails to match.
                        return criteria
                    criteria_list.append(criteria)
            # Flat alignment, the client engine's row-major flattening: the
            # sum range is the spine and each criteria range lines up with it
            # by index, so a function-returned matrix or a row range against
            # the sum column still compares every position.
            flat_sum = [cell for row in sum_matrix for cell in row]
            flat_criteria = [
                [cell for row in matrix for cell in row] for matrix in criteria_matrices
            ]
            total = 0.0
            for index in range(len(flat_sum)):
                all_match = True
                for ci in range(num_criteria):
                    crit = flat_criteria[ci]
                    if index < len(crit):
                        if not self._criteria_test(criteria_list[ci], crit[index]):
                            all_match = False
                            break
                    else:
                        all_match = False
                        break
                if all_match:
                    total += _to_number_or_zero(flat_sum[index])
            return float(total)
        if name == "COUNTIFS":
            if len(args) < 2 or len(args) % 2 != 0:
                return VALUE_ERROR
            num_criteria = len(args) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_node = args[i * 2]
                cr_matrix = self._criteria_range(cr_node, sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                range_error = _computed_range_error(cr_node, cr_matrix)
                if range_error is not None:
                    return range_error
                crit_node = args[i * 2 + 1]
                if crit_node[0] == "str":
                    criteria_list.append(crit_node[1])
                elif crit_node[0] == "num":
                    criteria_list.append(crit_node[1])
                else:
                    # A criteria argument is one value: scalarize a matrix the
                    # way the client engine does.
                    criteria = self._scalar_or(crit_node, sheet, seen, depth)
                    if is_error(criteria):
                        # An error criteria operand propagates; an error cell
                        # inside a criteria range only fails to match.
                        return criteria
                    criteria_list.append(criteria)
            if not criteria_matrices:
                return 0.0
            # Flat alignment, as in SUMIFS: the first criteria range is the
            # spine, matching the client engine's ``criteria[0].values``.
            flat_criteria = [
                [cell for row in matrix for cell in row] for matrix in criteria_matrices
            ]
            count = 0
            for index in range(len(flat_criteria[0])):
                all_match = True
                for ci in range(num_criteria):
                    crit = flat_criteria[ci]
                    if index < len(crit):
                        if not self._criteria_test(criteria_list[ci], crit[index]):
                            all_match = False
                            break
                    else:
                        all_match = False
                        break
                if all_match:
                    count += 1
            return float(count)
        if name == "AVERAGEIFS":
            if len(args) < 3 or len(args) % 2 != 1:
                return VALUE_ERROR
            average_range_node = args[0]
            average_matrix = self._criteria_range(
                average_range_node, sheet, seen, depth
            )
            if not average_matrix:
                return DIV_ZERO
            average_error = _computed_range_error(average_range_node, average_matrix)
            if average_error is not None:
                return average_error
            num_criteria = (len(args) - 1) // 2
            criteria_matrices = []
            criteria_list = []
            for i in range(num_criteria):
                cr_node = args[1 + i * 2]
                cr_matrix = self._criteria_range(cr_node, sheet, seen, depth)
                criteria_matrices.append(cr_matrix)
                range_error = _computed_range_error(cr_node, cr_matrix)
                if range_error is not None:
                    return range_error
                crit_node = args[2 + i * 2]
                if crit_node[0] == "str":
                    criteria_list.append(crit_node[1])
                elif crit_node[0] == "num":
                    criteria_list.append(crit_node[1])
                else:
                    # A criteria argument is one value: scalarize a matrix the
                    # way the client engine does.
                    criteria = self._scalar_or(crit_node, sheet, seen, depth)
                    if is_error(criteria):
                        # An error criteria operand propagates; an error cell
                        # inside a criteria range only fails to match.
                        return criteria
                    criteria_list.append(criteria)
            # Flat alignment, as in SUMIFS: the average range is the spine.
            flat_average = [cell for row in average_matrix for cell in row]
            flat_criteria = [
                [cell for row in matrix for cell in row] for matrix in criteria_matrices
            ]
            total = 0.0
            count = 0
            for index in range(len(flat_average)):
                all_match = True
                for ci in range(num_criteria):
                    crit = flat_criteria[ci]
                    if index < len(crit):
                        if not self._criteria_test(criteria_list[ci], crit[index]):
                            all_match = False
                            break
                    else:
                        all_match = False
                        break
                if all_match and _numeric(flat_average[index]):
                    # As in AVERAGEIF: only the numeric entries of the
                    # average range are averaged; text and blanks are
                    # ignored rather than counted as zero.
                    total += _to_number_or_zero(flat_average[index])
                    count += 1
            if count == 0:
                return DIV_ZERO
            return float(total) / count
        return VALUE_ERROR
