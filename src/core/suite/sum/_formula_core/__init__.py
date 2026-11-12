"""Formula evaluator core package.

Compatibility shim: the former ``_formula_core.py`` module is now a package.
``FormulaEvaluatorCore`` is composed from one mixin per responsibility
(evaluation, dispatch, values, text, lookup, criteria), and this namespace
also re-exports the function-family tables the formula parity tests read.

``FormulaEvaluatorAdvanced`` still imports ``FormulaEvaluatorCore`` from here,
so the composed ``FormulaEvaluator`` in ``formulas.py`` is unchanged.
"""

from .arity import _ARITY
from .base import _DependencyBudgetExceeded
from .criteria import CriteriaFunctionsMixin
from .dispatch import DispatchMixin
from .evaluation import EvaluationMixin
from .lookup import LookupFunctionsMixin
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
from .text import TextFunctionsMixin
from .values import ValueFunctionsMixin


class FormulaEvaluatorCore(
    EvaluationMixin,
    DispatchMixin,
    ValueFunctionsMixin,
    TextFunctionsMixin,
    LookupFunctionsMixin,
    CriteriaFunctionsMixin,
):
    """Bounded lazy evaluator over parsed formula ASTs."""


__all__ = [
    "FormulaEvaluatorCore",
    "_DependencyBudgetExceeded",
    "_ARITY",
    "_ADOPTED_SINGLE_FUNCTIONS",
    "_ADOPTED_STATISTICAL_FUNCTIONS",
    "_AGGREGATE_ERROR_FUNCTIONS",
    "_BRANCHING_FUNCTIONS",
    "_COMBINATORICS_FUNCTIONS",
    "_COMPLEX_FUNCTIONS",
    "_DISTRIBUTION_FUNCTIONS",
    "_EAGER_ARGUMENT_ERROR_EXCLUDED",
    "_ENGINEERING_FUNCTIONS",
    "_ERROR_TOLERANT_FUNCTIONS",
    "_FINANCIAL_FUNCTIONS",
    "_FLAT_ARGUMENT_ERROR_FUNCTIONS",
    "_SCALAR_SLOT_FUNCTIONS",
    "_SCALAR_SLOT_LEAD",
    "_STRUCTURED_FUNCTIONS",
    "_VALUE_FUNCTIONS",
]
