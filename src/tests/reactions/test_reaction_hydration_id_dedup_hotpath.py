"""Batch reaction hydration de-duplicates up to 100 message ids on EVERY call.

``get_reactions_batch`` validates/normalizes its id list through
``validate_bulk_message_ids`` before it builds the cache key and the SQL
placeholders.  That helper ran an O(n^2) ``x not in list`` scan: each accepted id
was compared against every id already accepted (4,950 equality comparisons for
a full 100-message page).  A single pass with a set removes that per-item work
without changing validation or the returned order.

This is a deterministic call-count proof of the algorithmic cost: a counting
``int`` subclass records every equality comparison the de-dup performs.
"""

from __future__ import annotations

import pytest

from src.core.reactions.manager import bounds


class _CountingInt(int):
    """Plain snowflake int that records equality comparisons it participates in."""

    comparisons = 0

    def __eq__(self, other):
        type(self).comparisons += 1
        return super().__eq__(other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return super().__hash__()


def test_bulk_message_id_dedup_is_single_pass(monkeypatch):
    ids = list(range(1_000_000_000_000_000_000, 1_000_000_000_000_000_100))
    real_parse_id = bounds.parse_id

    def counting_parse_id(value, field="id"):
        return _CountingInt(real_parse_id(value, field))

    monkeypatch.setattr(bounds, "parse_id", counting_parse_id)
    _CountingInt.comparisons = 0

    result = bounds.validate_bulk_message_ids(ids)
    comparisons = _CountingInt.comparisons

    # Order and values are unchanged.
    assert [int(x) for x in result] == ids
    # A set-based de-dup touches each id via its hash and performs no equality
    # scan. The old list-membership check compared every new id against every
    # id already accepted: n(n-1)/2 == 4,950 comparisons for 100 ids.
    assert comparisons < 200, (
        f"id de-dup did {comparisons} equality comparisons for {len(ids)} ids; "
        f"expected a single pass"
    )


def test_bulk_message_id_dedup_preserves_dedup_and_validation(monkeypatch):
    """The set-based pass must keep first-occurrence order and reject junk."""
    ids = [5, 3, 5, 3, 7]
    assert bounds.validate_bulk_message_ids(ids) == [5, 3, 7]

    with pytest.raises(ValueError):
        bounds.validate_bulk_message_ids([5, True])
    with pytest.raises(ValueError):
        bounds.validate_bulk_message_ids([5, 1.5])
    with pytest.raises(ValueError):
        bounds.validate_bulk_message_ids([5, -1])
