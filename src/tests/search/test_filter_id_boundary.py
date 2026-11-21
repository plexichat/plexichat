"""Search filter identifiers must use the canonical snowflake ID boundary.

``from:`` / ``mentions:`` / ``in:`` filter values are attacker-controlled query
text.  Before this fix the filter processor called bare ``int()`` on them, which
accepts Unicode decimal homoglyphs (``"٤٢"``, ``"４２"``), PEP 515 separators
(``"1_0"``), signs (``"-5"``), zero and values past 2**63-1.  Every other API
surface resolves identifiers through ``parse_id`` (ASCII digits only, positive,
snowflake-bounded), so the same token could be interpreted as a different id by
the filter layer than by storage, clients or the gateway.

This module pins the corrected behaviour: only canonical snowflake strings are
resolved numerically; everything else falls through to the parameterised
username/channel-name lookup instead of being coerced into an id.
"""

import pytest

from src.core.security.hardening import SNOWFLAKE_MAX
from src.core.search.query.filters import FilterProcessor

pytestmark = [pytest.mark.search, pytest.mark.unit]


class _RecordingDB:
    """Return no row, but remember every lookup so we can see the fall-through."""

    def __init__(self):
        self.lookups = []

    def fetch_one(self, sql, params):
        self.lookups.append((sql, params))
        return None


# Tokens that bare int() happily coerced but that are not canonical snowflakes.
NON_CANONICAL_IDS = (
    "\u0664\u0662",  # Arabic-Indic digits -> 42
    "\uff14\uff12",  # fullwidth digits -> 42
    "1_0",  # PEP 515 separator -> 10
    "-5",  # negative -> rejected by parse_id
    "0",  # zero -> rejected by parse_id
    str(SNOWFLAKE_MAX + 1),  # one past the snowflake ceiling
)


@pytest.mark.parametrize("token", NON_CANONICAL_IDS)
def test_resolve_user_never_coerces_non_canonical_token_to_id(token):
    db = _RecordingDB()
    processor = FilterProcessor(db)

    assert processor._resolve_user(token, caller_id=999) is None
    assert db.lookups, "non-canonical id must fall through to username lookup"
    assert db.lookups[-1][1] == (token,)
    assert token not in processor._user_cache


@pytest.mark.parametrize("token", NON_CANONICAL_IDS)
def test_resolve_channel_never_coerces_non_canonical_token_to_id(token):
    db = _RecordingDB()
    processor = FilterProcessor(db)

    assert processor._resolve_channel(token) is None
    assert db.lookups, "non-canonical id must fall through to name lookup"
    assert db.lookups[-1][1] == (token,)
    assert token not in processor._channel_cache


@pytest.mark.parametrize("token", ("1", "42", "123456789012345678", str(SNOWFLAKE_MAX)))
def test_resolve_user_keeps_canonical_snowflake_behaviour(token):
    db = _RecordingDB()
    processor = FilterProcessor(db)

    assert processor._resolve_user(token, caller_id=999) == int(token)
    assert not db.lookups, "a canonical id must not hit the username lookup"
    assert processor._user_cache[token] == int(token)


@pytest.mark.parametrize("token", ("1", "42", "123456789012345678", str(SNOWFLAKE_MAX)))
def test_resolve_channel_keeps_canonical_snowflake_behaviour(token):
    db = _RecordingDB()
    processor = FilterProcessor(db)

    assert processor._resolve_channel(token) == int(token)
    assert not db.lookups, "a canonical id must not hit the channel-name lookup"
    assert processor._channel_cache[token] == int(token)


def test_me_alias_still_resolves_to_caller_without_a_lookup():
    db = _RecordingDB()
    processor = FilterProcessor(db)

    for alias in ("me", "@me", " ME "):
        assert processor._resolve_user(alias, caller_id=777) == 777
    assert not db.lookups
