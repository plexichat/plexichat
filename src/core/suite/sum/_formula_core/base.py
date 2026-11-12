"""Shared state and member contract for the formula-core mixins.

``FormulaEvaluatorCore`` is composed from one mixin per responsibility. This
module owns the evaluator's state and declares every method the mixins call
through ``self``, so no mixin imports another one. The advanced-function half
of the surface is declared by ``FormulaEvaluatorProtocol``.
"""

from typing import Any, Dict, List, Optional, Tuple

from .._formula_protocol import FormulaEvaluatorProtocol
from .._formula_shared import MAX_DEPENDENCIES, CellResolver


class _DependencyBudgetExceeded(Exception):
    """The per-formula dependency budget ran out (a runaway cycle).

    Raised instead of the formula errors ``_call`` turns into values, so the
    ``_call`` wrapper never swallows it: the abort has to unwind the whole
    formula rather than become a #NUM! an enclosing error-tolerant function
    could then skip.
    """


class FormulaEvaluatorCoreBase(FormulaEvaluatorProtocol):
    """Evaluator state plus the cross-mixin method contract."""

    def __init__(
        self,
        resolver: CellResolver,
        dependencies: int = MAX_DEPENDENCIES,
        named_ranges: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._resolver = resolver
        self._dependencies = dependencies
        self._used = 0
        self._named_ranges = named_ranges or {}
        # Cache key includes the formula text: the same coordinates may be
        # evaluated with a different formula when the evaluator is reused
        # (e.g. ``compute_value`` evaluates arbitrary cells, and a cell's
        # formula may change between reads). Keying on the formula keeps the
        # memoization sound in every reuse scenario.
        self._computed: Dict[Tuple[Optional[str], int, int, str], Any] = {}

    # -- Implemented by the behaviour mixins ------------------------------
    def evaluate_cell(
        self,
        sheet: Optional[str],
        row: int,
        column: int,
        formula: str,
        seen: Optional[set] = None,
        depth: int = 0,
    ) -> Any:
        raise NotImplementedError

    def _resolve(
        self, sheet: Optional[str], row: int, column: int, seen: set, depth: int
    ) -> Any:
        raise NotImplementedError

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
        raise NotImplementedError

    def _eval(self, node: Any, sheet: Optional[str], seen: set, depth: int) -> Any:
        raise NotImplementedError

    def _operand(self, node: Any, sheet: Optional[str], seen: set, depth: int) -> Any:
        raise NotImplementedError

    def _apply(self, op: str, left: Any, right: Any) -> Any:
        raise NotImplementedError

    def _call(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        raise NotImplementedError

    def _expand_arg(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> Tuple[List[Any], bool]:
        raise NotImplementedError

    def _argument_error(
        self,
        args: List[Any],
        sheet: Optional[str],
        seen: set,
        depth: int,
        defer_references: bool = False,
    ) -> Optional[str]:
        raise NotImplementedError

    def _truthy(self, value: Any) -> bool:
        raise NotImplementedError

    def _aggregate(
        self, name: str, values: List[Any], from_reference: List[bool]
    ) -> Any:
        raise NotImplementedError

    def _single(self, name: str, values: List[Any]) -> Any:
        # Fixed arity: a stray argument is #VALUE!, the way the client
        # engine answers it. Reading only the first value silently agreed
        # on every valid call while disagreeing on ABS(1, 2) and friends.
        raise NotImplementedError

    def _information(self, name: str, values: List[Any]) -> Any:
        raise NotImplementedError

    def _scalar_slot(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> Tuple[Optional[str], Any]:
        raise NotImplementedError

    def _scalar_or(self, node: Any, sheet: Optional[str], seen: set, depth: int) -> Any:
        raise NotImplementedError

    @staticmethod
    def _coerce_slot(value: Any) -> Tuple[Optional[str], Optional[float]]:
        raise NotImplementedError

    def _double_args(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        raise NotImplementedError

    def _text_slice(self, name: str, values: List[Any]) -> Any:
        raise NotImplementedError

    def _range_matrix(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> List[List[Any]]:
        raise NotImplementedError

    def _criteria_range(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> List[List[Any]]:
        raise NotImplementedError

    @staticmethod
    def _lookup_eq(a: Any, b: Any, wildcards: bool = False) -> bool:
        raise NotImplementedError

    def _lookup_value(
        self, node: Any, sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        raise NotImplementedError

    def _lookup(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        raise NotImplementedError

    def _criteria(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        raise NotImplementedError

    @staticmethod
    def _criteria_test(criteria: Any, value: Any) -> bool:
        raise NotImplementedError
