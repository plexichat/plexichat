"""DM block filter: strict ids for the audience, tolerant ids for block sets.

``_dm_participants_without_blocks`` uses one ``_as_id()`` helper for two very
different jobs:

* building the **positive participant audience**, where a malformed-but-
  coercible id (``"05"``->5, ``7.9``->7, ``True``->1) must NOT be admitted as a
  principal that never proved participation; and
* comparing **block-set ids** (``rel_blocked.blocker_id``/``blocked_id``), where
  tolerant normalization must stay so a string-stored legacy block id still
  blocks correctly (tightening it would fail open on blocking).

The audience side must require genuine ``int``; the block-comparison side must
keep the tolerant coercion.
"""

import pytest

import src.api as api
from src.core.events.gateway_emit import _dm_participants_without_blocks

pytestmark = pytest.mark.unit


class _Relationships:
    def __init__(self, blocks_by_user=None):
        self._blocks = blocks_by_user or {}

    def get_all_blocked_ids(self, user_id):
        # Values may be strings from a legacy import; blocking must still work.
        return list(self._blocks.get(int(user_id), []))


def _wire(monkeypatch, blocks_by_user=None):
    monkeypatch.setattr(
        api, "get_relationships", lambda: _Relationships(blocks_by_user)
    )


def test_malformed_participant_ids_are_not_admitted(monkeypatch):
    _wire(monkeypatch)
    raw = ["05", 7, " 8 ", True, 5.9, 42]

    got = _dm_participants_without_blocks(raw, 999)

    assert got == [7, 42]
    for wrong in (5, 8, 1):
        assert wrong not in got, f"laundered participant id {wrong} admitted"


def test_string_stored_block_ids_still_block(monkeypatch):
    # Actor 1 blocked participant 8 (string id); participant 7 blocked the
    # actor (string id). Both directions must still exclude those users.
    _wire(monkeypatch, {1: ["8"], 7: ["1"], 8: [], 42: []})
    participants = [1, 7, 8, 42]

    got = _dm_participants_without_blocks(participants, 1)

    # The actor (1) is stripped later by the resolver's _final(), not here.
    assert got == [1, 42]
    assert 7 not in got  # participant 7 blocked the actor ("1" string)
    assert 8 not in got  # actor blocked participant 8 ("8" string)


def test_genuine_int_participants_preserved(monkeypatch):
    # Non-vacuous: a clean all-int audience passes through intact.
    _wire(monkeypatch)
    assert _dm_participants_without_blocks([10, 20, 30, 20], 999) == [10, 20, 30]
