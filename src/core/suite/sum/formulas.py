"""Bounded formula evaluator for PlexiSum.

Formulas are stored as raw text (after the ``=``) in ``sum_cells.formula``
and evaluated on read against a resolver supplied by the caller. The engine
supports arithmetic, comparisons, string concatenation, cell references,
ranges, cross-sheet references, and a compact set of common functions.

Every evaluation is bounded: a maximum dependency count, a maximum call
depth, and cycle detection guarantee a single malicious or accidental formula
cannot stall a viewport read. Errors propagate as ``#...`` strings.

The engine is deliberately dependency-free and deterministic so it can be
unit-tested in isolation and reused by viewport reads, CSV export, and sort.

This module is the stable import facade: the parser/tokenizer and coercion
helpers live in ``_formula_shared.py`` and the evaluator methods are split
across the ``_formula_core/`` / ``_formula_advanced/`` mixin packages.

Semantics follow Excel, and are shared with the PlexiSum client engine: the
same case table (``src/tests/suite/formula_parity_cases.json``) is
evaluated against both engines so neither can drift unnoticed. In short, an
error operand propagates out of a function (``SUM(1, 1/0)`` is ``#DIV/0!``)
except for ``COUNT``/``COUNTA``/``COUNTBLANK`` and the ``IS*`` family, which
classify it; a text *argument* is coerced to a number (``SUM("2")`` is 2)
while a value read from a reference is not (``SUM(A1)`` is 0 when A1 holds the
text ``"2"``); and criteria comparisons rank types (number < text < ``FALSE`` <
``TRUE``) with text compared case-insensitively. The README lists the
deliberate divergences from Excel.
"""

from typing import Any, Dict, Optional

from ._formula_advanced import FormulaEvaluatorAdvanced
from ._formula_core import FormulaEvaluatorCore
from ._formula_shared import (
    CYCLE_ERROR as CYCLE_ERROR,
    DIV_ZERO as DIV_ZERO,
    ERROR_CODES as ERROR_CODES,
    MAX_CALL_DEPTH as MAX_CALL_DEPTH,
    MAX_DEPENDENCIES as MAX_DEPENDENCIES,
    N_A_ERROR as N_A_ERROR,
    NAME_ERROR as NAME_ERROR,
    NUM_ERROR as NUM_ERROR,
    REF_ERROR as REF_ERROR,
    VALUE_ERROR as VALUE_ERROR,
    CellResolver as CellResolver,
    _Parser as _Parser,
    _coerce_number_text as _coerce_number_text,
    _fold_case as _fold_case,
    _to_text as _to_text,
    _tokenize as _tokenize,
    is_error as is_error,
    parse_cell_ref as parse_cell_ref,
    parse_formula as parse_formula,
)


# Named ranges are workbook-controlled data: ``parse_cell_ref`` follows a
# named range's definition recursively, so a self-reference or a cycle such as
# {"A": "B", "B": "A"} exhausted the Python stack with a ``RecursionError``.
# The evaluator's reference handlers only catch ``ValueError``, so that
# exception escaped the engine and could take down a viewport/export read.
# Resolve every definition down to a concrete cell reference before handing
# the table to the evaluator, and drop definitions that cycle, run too long,
# or never terminate. Referencing a dropped name still answers #REF! through
# the normal path; it just cannot recurse forever.
_MAX_NAMED_RANGE_HOPS = 128


def _safe_named_ranges(named_ranges: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """Flatten named-range aliases into bounded, acyclic cell references."""
    if not named_ranges:
        return {}
    safe: Dict[str, str] = {}

    def is_direct_ref(text: str) -> bool:
        try:
            parse_cell_ref(text)
        except (ValueError, TypeError):
            return False
        return True

    for name, definition in named_ranges.items():
        if not isinstance(name, str) or not isinstance(definition, str):
            continue
        current: Optional[str] = definition
        seen: set = set()
        for _ in range(_MAX_NAMED_RANGE_HOPS):
            if current is None:
                break
            key = current.upper()
            if key in seen:
                # Cycle: drop the alias so it resolves to #REF!, not a loop.
                current = None
                break
            seen.add(key)
            if is_direct_ref(current):
                safe[name] = current
                break
            following = named_ranges.get(key)
            if not isinstance(following, str):
                current = None
                break
            current = following
    return safe


class FormulaEvaluator(FormulaEvaluatorAdvanced, FormulaEvaluatorCore):
    """Bounded lazy evaluator over parsed formula ASTs."""

    def __init__(
        self,
        resolver: CellResolver,
        dependencies: int = MAX_DEPENDENCIES,
        named_ranges: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            resolver,
            dependencies=dependencies,
            named_ranges=_safe_named_ranges(named_ranges),
        )


def compute_value(
    value_type: str,
    value: Any,
    formula: Optional[str],
    resolver: CellResolver,
    sheet: Optional[str] = None,
    named_ranges: Optional[Dict[str, Any]] = None,
    _evaluator_cache: Optional[Dict[int, FormulaEvaluator]] = None,
) -> Any:
    """Top-level entry: return the display/computed value for a cell.

    ``sheet`` defaults to ``None`` (single-sheet context); callers that
    resolve against a real workbook should pass the sheet id so cross-sheet
    and same-sheet references hit the right rows.

    An optional *named_ranges* dict maps range names (case-insensitive) to
    cell references (e.g., ``{"SALES": "A1:A100"}``).

    An optional *_evaluator_cache* dict (keyed by ``id(resolver)``) allows
    callers that evaluate many cells with the same resolver to reuse a
    single ``FormulaEvaluator`` instance, preserving its memoization cache
    across calls.  Without this, every ``compute_value`` call created a
    fresh evaluator, discarding previously computed results.
    """
    if value_type == "formula" and formula:
        if _evaluator_cache is not None:
            key = id(resolver)
            evaluator = _evaluator_cache.get(key)
            if evaluator is None:
                evaluator = FormulaEvaluator(resolver, named_ranges=named_ranges)
                _evaluator_cache[key] = evaluator
            return evaluator.evaluate_cell(sheet, -1, -1, formula)
        return FormulaEvaluator(resolver, named_ranges=named_ranges).evaluate_cell(
            sheet, -1, -1, formula
        )
    return value


__all__ = [
    "CYCLE_ERROR",
    "DIV_ZERO",
    "ERROR_CODES",
    "FormulaEvaluator",
    "MAX_CALL_DEPTH",
    "MAX_DEPENDENCIES",
    "N_A_ERROR",
    "NAME_ERROR",
    "NUM_ERROR",
    "REF_ERROR",
    "VALUE_ERROR",
    "CellResolver",
    "compute_value",
    "is_error",
    "parse_formula",
]
