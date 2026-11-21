"""Server owner gate must not launder a malformed owner row into the cache.

``MemberCrudMixin.is_member`` reads the authoritative ``srv_servers.owner_id``
row and does ``owner_id = int(row["owner_id"])``, then primes the shared
``_server_owner_cache_prefix`` with that value and grants owner-level
membership via ``owner_id == uid``. A malformed-but-coercible value ("05",
" 5 ", 5.9, True) is coerced into a genuine-looking int, so a non-owner is
granted membership on the DB-hit call -- and the laundered int stays cached,
reopening the grant on every later cache hit. Only a genuine integer id from
the authoritative row may attest to ownership or be primed into that cache.
"""

import pytest

from src.core.database import cache_get, cache_set, valkey_available
from src.core.servers.handlers.members.crud import MemberCrudMixin

pytestmark = pytest.mark.unit


class _Mgr:
    _member_cache_prefix = "member"
    _server_owner_cache_prefix = "server_owner"

    def __init__(self):
        self.mem = {}
        self.owner_primes = []

    def _cache_get(self, prefix, key):
        return self.mem.get((prefix, key))

    def _cache_set(self, prefix, key, value):
        self.mem[(prefix, key)] = value
        if prefix == self._server_owner_cache_prefix:
            self.owner_primes.append(value)


class _DB:
    def __init__(self, owner_value, member_row=None):
        self.owner_value = owner_value
        self.member_row = member_row

    def fetch_one(self, query, params=None):
        if "srv_servers" in query:
            return {"owner_id": self.owner_value}
        if "srv_members" in query:
            return self.member_row
        return None


def _run(monkeypatch, owner_value, uid):
    # Isolate from Valkey so the in-memory owner cache path is exercised.
    monkeypatch.setattr("src.core.database.valkey_available", lambda: False)
    service = MemberCrudMixin()
    service.manager = _Mgr()
    service.db = _DB(owner_value)
    allowed = service.is_member(777, uid)
    return allowed, service.manager.owner_primes


_MALFORMED = [
    ("05", 5),  # leading-zero string: int() -> 5
    (" 5 ", 5),  # padded string
    (5.9, 5),  # float truncates to 5
    ("5", 5),  # plain numeric string
    (True, 1),  # bool coerces to 1
]


@pytest.mark.parametrize("owner_value, coerced_id", _MALFORMED)
def test_malformed_owner_row_does_not_grant_membership(monkeypatch, owner_value, coerced_id):
    allowed, _ = _run(monkeypatch, owner_value, coerced_id)
    assert allowed is False, (
        f"malformed owner_id {owner_value!r} was coerced into owner-level "
        f"membership for user {coerced_id}"
    )


@pytest.mark.parametrize("owner_value, coerced_id", _MALFORMED)
def test_malformed_owner_is_not_laundered_into_owner_cache(
    monkeypatch, owner_value, coerced_id
):
    """A coerced value must never be primed as a genuine owner id."""
    _, primes = _run(monkeypatch, owner_value, coerced_id)
    assert primes == [], (
        f"malformed owner_id {owner_value!r} was laundered into genuine-int "
        f"owner cache value(s): {primes}"
    )


def test_genuine_integer_owner_still_grants(monkeypatch):
    """Non-vacuity: the real owner still gets membership and a genuine prime."""
    allowed, primes = _run(monkeypatch, 5, 5)
    assert allowed is True
    assert primes == [5]


def test_non_owner_integer_row_fails_closed(monkeypatch):
    allowed, primes = _run(monkeypatch, 7, 5)
    assert allowed is False
    assert primes == [7]  # genuine ints are still cached for real owners
