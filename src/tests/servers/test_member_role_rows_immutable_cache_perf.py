"""PERF: cached authorization role rows must not be copied on every read.

``get_cached_member_role_rows`` is the L0 memo behind every role-hierarchy and
permission decision (bans, voice moderation, automod, channel listings). Each
hit used to run ``[dict(row) for row in value]`` -- a fresh dict for *every*
row on *every* authorization check, even though callers only read the rows.

Cache the rows as immutable mappings instead and hand the same frozen value
back on each hit: the per-read copy disappears, a caller mutation attempt
raises instead of poisoning the memo, and fail-closed miss handling is
untouched.
"""

from __future__ import annotations

import pytest

from src.core.servers.manager import member_role_rows_cache as mrc

pytestmark = [pytest.mark.unit, pytest.mark.servers]

_ROWS = [
    {"id": 1, "name": "mod", "position": 5, "permissions": {"messages.send": True}},
    {"id": 2, "name": "admin", "position": 10, "permissions": {"admin.roles": True}},
]


@pytest.fixture(autouse=True)
def _clean():
    mrc.invalidate_member_role_rows_cache()
    yield
    mrc.invalidate_member_role_rows_cache()


def _warm() -> None:
    mrc.prime_member_role_rows(1, 10, [dict(row) for row in _ROWS])


def test_no_per_read_row_copies(monkeypatch):
    """N cache hits allocate 0 row dicts (before the fix: N * len(rows))."""
    _warm()
    # Sanity: the entry is really a cache hit, not a miss that reloads.
    assert mrc.get_cached_member_role_rows(1, 10)

    real_dict = dict
    calls = {"n": 0}

    def counting_dict(*args, **kwargs):
        calls["n"] += 1
        return real_dict(*args, **kwargs)

    # Shadowing the module global counts only this module's dict() calls.
    monkeypatch.setattr(mrc, "dict", counting_dict, raising=False)

    n = 100
    for _ in range(n):
        rows = mrc.get_cached_member_role_rows(1, 10)
        assert len(rows) == len(_ROWS)

    assert calls["n"] == 0, (
        f"cached role rows were copied {calls['n']} times across {n} reads "
        f"(uncached baseline {n * len(_ROWS)}); the memo must hand back "
        "immutable rows with no per-read copy"
    )


def test_returned_rows_are_immutable_and_isolated():
    """A caller mutation attempt fails and cannot poison later authz reads."""
    _warm()
    first = mrc.get_cached_member_role_rows(1, 10)

    with pytest.raises(TypeError):
        first[0]["name"] = "tampered"
    with pytest.raises(TypeError):
        first[0]["permissions"]["admin.roles"] = False
    with pytest.raises((AttributeError, TypeError)):
        first.append({"id": 999})

    second = mrc.get_cached_member_role_rows(1, 10)
    assert [dict(r) for r in second] == _ROWS
