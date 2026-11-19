"""Regression: two route boundaries still parsed snowflakes with bare ``int()``.

``GET /users/{user_id}`` (public profile lookup) and the ``server_id`` filter of
``GET /users/@me/audit-logs`` used ``int(raw)`` instead of the shared snowflake
gate. Python's ``int()`` accepts Unicode decimal homoglyphs (``"١٢٣"`` -> 123),
PEP 515 separators (``"1_2"`` -> 12), explicit signs/whitespace and negative
values over the whole signed 64-bit range. Every other route boundary rejects
those spellings, so an upstream hop that validates the id as ASCII-only text
can reject a value that these routes silently resolved to a real user/server
(a parser differential). The unbounded negative/oversized forms also reached
the lookup/SQL layer unchanged.

Both routes now use the shared ``parse_id`` gate (ASCII digits, 1..2**63-1),
keeping the routes' 400 contract. These tests pin that contract without a live
database.

The canonical ``parse_id``/``SnowflakeID`` grammar intentionally tolerates a
leading ``+`` and surrounding whitespace (normalised by ``bounded_int``), so
those spellings are not asserted as rejected for the public-user lookup. The
feature-route gate used by the audit-log filter is stricter and rejects a
leading ``+``.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

USER = SimpleNamespace(user_id=123, token_type="user")

# Spellings int() accepts but no snowflake boundary may accept: unicode digit
# homoglyphs, PEP 515 separators, negatives/zero and out-of-range values. The
# canonical parse_id (used by public user lookup) deliberately still tolerates
# a leading "+" and surrounding whitespace, as does the SnowflakeID schema.
LOOSE_IDS = [
    "-5",
    "0",
    "1_2",
    "12.5",
    "١٢٣",  # Arabic-Indic digits
    "１２３",  # fullwidth digits
    "9" * 25,  # past the signed 64-bit ceiling
    "9" * 5000,  # past CPython's int() digit-conversion cap
]

# The feature-route gate (used by the audit-log filter) is stricter still and
# rejects a leading "+" (both gates strip surrounding whitespace, so " 12 "
# is intentionally not in the rejection set).
FEATURE_STRICT_EXTRA = ["+12"]


# ---------------------------------------------------------------------------
# GET /users/{user_id}  (ProfileMixin.get_user)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", LOOSE_IDS)
async def test_public_user_lookup_rejects_loose_id_forms(monkeypatch, bad_id):
    import src.api.routes.users.profile as mod

    calls = []

    class _FakeAuth:
        def get_user_profiles_bulk(self, ids):
            calls.append(ids)
            return {}

    monkeypatch.setattr(mod.api, "get_auth", lambda: _FakeAuth())

    with pytest.raises(HTTPException) as exc:
        await mod.ProfileMixin().get_user(bad_id, current_user=USER)
    assert exc.value.status_code == 400
    assert calls == [], "no profile lookup may run for a malformed id"


@pytest.mark.asyncio
async def test_public_user_lookup_accepts_canonical_snowflake(monkeypatch):
    import src.api.routes.users.profile as mod

    calls = []

    class _FakeAuth:
        def get_user_profiles_bulk(self, ids):
            calls.append(ids)
            return {}  # no such user -> the route's honest 404

    monkeypatch.setattr(mod.api, "get_auth", lambda: _FakeAuth())

    with pytest.raises(HTTPException) as exc:
        await mod.ProfileMixin().get_user("9007199254740993", current_user=USER)
    # The id cleared the gate and reached the lookup (which found no user).
    assert exc.value.status_code == 404
    assert calls == [[9007199254740993]]


# ---------------------------------------------------------------------------
# GET /users/@me/audit-logs  (server_id filter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", LOOSE_IDS + FEATURE_STRICT_EXTRA)
async def test_audit_log_server_filter_rejects_loose_id_forms(monkeypatch, bad_id):
    import src.api.routes.features.audit_logs as mod

    calls = []

    class _FakeDB:
        def fetch_all(self, query, params):
            calls.append((query, params))
            return []

    class _FakeServers:
        def get_servers(self, user_id):
            return []  # no visible servers: the filter must still be validated

    monkeypatch.setattr(mod.api, "get_db", lambda: _FakeDB())
    monkeypatch.setattr(mod.api, "get_servers", lambda: _FakeServers())

    with pytest.raises(HTTPException) as exc:
        await mod.get_user_audit_logs(server_id=bad_id, current_user=USER)
    assert exc.value.status_code == 400
    assert calls == [], "no audit-log query may run for a malformed server id"


@pytest.mark.asyncio
async def test_audit_log_server_filter_accepts_canonical_snowflake(monkeypatch):
    import src.api.routes.features.audit_logs as mod

    class _FakeDB:
        def fetch_all(self, query, params):
            return []

    class _FakeServers:
        def get_servers(self, user_id):
            return []

    monkeypatch.setattr(mod.api, "get_db", lambda: _FakeDB())
    monkeypatch.setattr(mod.api, "get_servers", lambda: _FakeServers())

    result = await mod.get_user_audit_logs(
        server_id="9007199254740993", current_user=USER
    )
    assert result == {"entries": []}
