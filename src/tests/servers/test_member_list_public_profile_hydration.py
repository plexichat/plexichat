"""Member roster hydration uses the narrow, cached public-profile bulk lookup.

``GET /servers/{id}/members`` renders only ``username``, ``avatar_url`` and
``badges`` for each member. It used to call ``auth.get_users_bulk``: an
uncached ``SELECT u.* LEFT JOIN user_features`` that decrypts every user's
email (and date of birth) on each cold roster read, even though neither field
is serialized. The profile cache's narrow ``get_user_profiles_bulk`` returns
exactly those public fields with no PII decryption. These tests pin that the
roster takes the narrow path, that no private field is decrypted on a cold
page, and that the legacy bulk lookup still works as a fallback.
"""

import sys
import types
from pathlib import Path
from unittest.mock import patch

# Load the members route module in isolation. Importing the real
# ``src.api.routes.servers`` package executes every sibling route module's
# __init__, which is unrelated to this test and can transiently fail when other
# work is in flight. Stubbing the two package __init__ levels keeps ``members``
# and ``roles`` loading from disk while skipping the sibling route imports.
_SRC_API = Path(__file__).resolve().parents[2] / "api"
_routes_pkg = types.ModuleType("src.api.routes")
_routes_pkg.__path__ = [str(_SRC_API / "routes")]
_routes_pkg.create_api_router = lambda *a, **k: object()
_routes_pkg.create_docs_router = lambda *a, **k: object()
_routes_pkg.is_docs_enabled = lambda *a, **k: False
sys.modules.setdefault("src.api.routes", _routes_pkg)
_servers_pkg = types.ModuleType("src.api.routes.servers")
_servers_pkg.__path__ = [str(_SRC_API / "routes" / "servers")]
_servers_pkg.router = None
sys.modules.setdefault("src.api.routes.servers", _servers_pkg)

from src.api.routes.servers import members  # noqa: E402
from src.core.database import invalidate_pattern  # noqa: E402
from src.core.database.cache.base import mem_cache_clear_pattern  # noqa: E402


class TokenInfo:
    def __init__(self, user_id):
        self.user_id = user_id
        self.token_type = "user"
        self.account_id = None


def _member(idx, user_id):
    return {
        "id": 50_000 + idx,
        "user_id": user_id,
        "nickname": None,
        "joined_at": 1,
        "roles": [],
        "timeout_until": None,
        "timeout_reason": None,
    }


class _FakeServers:
    def __init__(self, rows):
        self.rows = rows

    def get_members(self, viewer, sid, limit, after):
        return list(self.rows[:limit])


def _wire(monkeypatch, servers, auth):
    monkeypatch.setattr(members.api, "get_servers", lambda: servers)
    monkeypatch.setattr(members.api, "get_auth", lambda: auth)
    monkeypatch.setattr(members.api, "get_presence", lambda: None)
    invalidate_pattern("server_members_api:*")
    mem_cache_clear_pattern("*")


def test_member_list_uses_narrow_profile_hydration(monkeypatch):
    """The roster resolves public fields via get_user_profiles_bulk only."""
    calls = {"profiles": [], "users": []}

    class Auth:
        def get_user_profiles_bulk(self, ids):
            calls["profiles"].append(list(ids))
            # str keys, exactly how the real bulk API reports profiles.
            return {
                str(uid): {
                    "id": uid,
                    "username": f"user_{uid}",
                    "avatar_url": f"/api/v1/avatars/users/{uid}",
                    "badges": ["vip"],
                }
                for uid in ids
            }

        def get_users_bulk(self, ids):
            calls["users"].append(list(ids))  # must not be reached
            return {}

    rows = [_member(i, uid) for i, uid in enumerate((11, 12, 11))]
    _wire(monkeypatch, _FakeServers(rows), Auth())
    token = TokenInfo(7)

    payload = members.get_server_members("1", 100, None, token)

    # Distinct ids only, first-seen order; one bulk call, no legacy wide lookup.
    assert calls["profiles"] == [[11, 12]]
    assert calls["users"] == []
    assert len(payload.items) == 3
    for item in payload.items:
        assert item.username in {"user_11", "user_12"}
        assert item.avatar_url.startswith("/api/v1/avatars/users/")
        assert item.badges == ["vip"]


def test_member_list_hydration_falls_back_to_legacy_bulk(monkeypatch):
    """Auth objects without the narrow API keep working via get_users_bulk."""

    class LegacyUser:
        def __init__(self, uid):
            self.username = f"legacy_{uid}"
            self.avatar_url = f"/avatars/{uid}"
            self.badges = ["legacy"]

    class LegacyAuth:
        def __init__(self):
            self.calls = []

        def get_users_bulk(self, ids):
            self.calls.append(list(ids))
            return {uid: LegacyUser(uid) for uid in ids}

    auth = LegacyAuth()
    _wire(monkeypatch, _FakeServers([_member(0, 11), _member(1, 12)]), auth)

    payload = members.get_server_members("2", 100, None, TokenInfo(7))

    assert auth.calls == [[11, 12]]
    assert [i.username for i in payload.items] == ["legacy_11", "legacy_12"]
    assert payload.items[0].avatar_url == "/avatars/11"
    assert payload.items[0].badges == ["legacy"]


def test_cold_member_list_decrypts_no_private_fields(db, auth_manager, monkeypatch):
    """A cold 20-member page must not decrypt any PII for hydration.

    Before the fix this did 20 decrypt_data calls (one email per member) plus
    a wide SELECT u.* query; the narrow cached lookup does zero decrypts.
    """
    ids = []
    for i in range(20):
        user = auth_manager.register(
            username=f"hydration_member_{i}",
            email=f"hydration_member_{i}@example.test",
            password="HydratePass123!",
        )
        ids.append(user.id)

    _wire(
        monkeypatch,
        _FakeServers([_member(i, uid) for i, uid in enumerate(ids)]),
        auth_manager,
    )

    mgr = auth_manager._manager
    real_decrypt = mgr.crypto.decrypt_data
    with patch.object(mgr.crypto, "decrypt_data", wraps=real_decrypt) as spy:
        payload = members.get_server_members("3", 100, None, TokenInfo(7))
        decrypt_calls = spy.call_count

    assert len(payload.items) == 20
    assert decrypt_calls == 0
