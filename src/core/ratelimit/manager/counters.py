"""Custom usage counters.

Callers use these for non-request budgets such as upload bytes or daily
reports. The counter is applied under the backend lock so concurrent
increments cannot lose updates.
"""

from typing import Any, Dict


def apply_custom_increment(
    state: Dict[str, Any],
    field: str,
    amount: int,
    outcome: Dict[str, int],
) -> None:
    """Add ``amount`` to ``state[field]`` and record the new value."""
    current = state.get(field, 0)
    try:
        current = int(current)
    except (TypeError, ValueError):
        current = 0
    new_value = current + amount
    state[field] = new_value
    outcome["value"] = new_value
