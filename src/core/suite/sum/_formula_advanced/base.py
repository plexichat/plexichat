"""Shared base for advanced formula mixins and argument evaluation."""

from typing import Any, List, Optional
from .._formula_core import FormulaEvaluatorCore
from .helpers.numbers import _read_number


class AdvancedEvaluatorBase(FormulaEvaluatorCore):
    """Declarations and helpers shared by every advanced mixin."""

    def _argument_list(
        self, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> List[Any]:
        """Evaluate each argument into its own slot.

        A scalar argument becomes its value, a whole range becomes a flat list
        -- the shape the client engine receives and flattens with ``.flat()``
        -- and an omitted argument becomes a blank, so a caller can test its
        position the way the client tests ``args.length``.
        """
        out: List[Any] = []
        for node in args:
            kind = node[0]
            if kind == "blank":
                out.append(None)
                continue
            if kind == "ref" and ":" in node[1]:
                expanded, _ = self._expand_arg(node, sheet, seen, depth)
                out.append(expanded)
                continue
            out.append(self._eval(node, sheet, seen, depth))
        return out

    def _number_arguments(self, values: List[Any]) -> tuple:
        """Coerce a whole argument list to numbers, as the client's toNumber does.

        Returns ``(error, numbers)``. A range where a scalar belongs is
        #VALUE!, the same answer the client's ``toNumber`` gives an array.
        """
        numbers: List[float] = []
        for value in values:
            error, number = _read_number(value)
            if error is not None:
                return error, None
            numbers.append(number)
        return None, numbers
