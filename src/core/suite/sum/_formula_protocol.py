"""Shared member surface for the formula evaluator mixins.

``FormulaEvaluator`` composes ``FormulaEvaluatorAdvanced`` and
``FormulaEvaluatorCore``; the core dispatcher calls into advanced-function
helpers declared here so both halves type-check (same pattern as the voice
manager protocol). Real implementations always win: in the composed MRO
``FormulaEvaluatorAdvanced`` precedes this protocol.
"""

from typing import Any, List, Optional


class FormulaEvaluatorProtocol:
    def _substitute(self, values: List[Any]) -> Any:
        raise NotImplementedError

    def _date(self, name: str, values: List[Any]) -> Any:
        raise NotImplementedError

    def _days(self, values: List[Any]) -> Any:
        raise NotImplementedError

    def _lookup_advanced(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        raise NotImplementedError

    def _advanced_aggregate(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        raise NotImplementedError

    def _advanced_text(self, name: str, values: List[Any]) -> Any:
        raise NotImplementedError

    def _advanced_date(self, name: str, values: List[Any]) -> Any:
        raise NotImplementedError

    def _advanced_trig(self, name: str, values: List[Any]) -> Any:
        raise NotImplementedError

    def _advanced_criteria(
        self, name: str, args: List[Any], sheet: Optional[str], seen: set, depth: int
    ) -> Any:
        raise NotImplementedError


__all__ = ["FormulaEvaluatorProtocol"]
