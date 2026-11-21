"""Membership revocation must evict every per-viewer read cache.

Kick/ban/leave all call ``_invalidate_member_access_caches``, but that helper
used to clear only the roster, joined-server, channel, sticker and soundboard
namespaces. The role listing (300s TTL) and emoji list/detail reads (10s TTL)
are filled behind a membership check, so a removed member kept receiving a
cached 200 -- including role permission data -- long after the revocation
committed. These tests pin both the eviction itself and its use on every
revocation path.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.database.cache.base import mem_cache_matching_keys

pytestmark = [pytest.mark.unit, pytest.mark.security]


class TokenInfo:
    """Minimal stand-in with the real class name used by cache key builders."""

    def __init__(self, user_id, token_type="user", account_id=None):
        self.user_id = user_id
        self.token_type = token_type
        self.account_id = account_id


class _FakeServers:
    def get_roles(self, user_id, server_id):
        return []

    def get_member(self, server_id, user_id):
        return object()


class _FakeReactions:
    def get_server_custom_emojis(self, sid):
        return []

    def get_custom_emoji(self, eid):
        return SimpleNamespace(
            id=eid,
            server_id=123,
            name="hello",
            animated=False,
            url="",
            available=True,
            created_by=7,
            uploader_username="tester",
            created_at=1,
        )


def test_revocation_drops_role_and_emoji_caches(monkeypatch):
    """The cache keys the gated reads create must all be gone after a kick."""
    import asyncio

    from src.api.routes import emojis
    from src.api.routes.servers import members, roles as roles_routes

    servers = _FakeServers()
    reactions = _FakeReactions()
    monkeypatch.setattr(emojis.api, "get_servers", lambda: servers)
    monkeypatch.setattr(emojis.api, "get_reactions", lambda: reactions)
    monkeypatch.setattr(roles_routes.api, "get_servers", lambda: servers)

    token = TokenInfo(7)
    asyncio.run(roles_routes.get_server_roles(server_id="123", current_user=token))
    asyncio.run(emojis.get_server_emojis(server_id="123", current_user=token))
    asyncio.run(emojis.get_emoji(server_id="123", emoji_id="9", current_user=token))

    assert mem_cache_matching_keys("server_roles_api:*"), "roles cache was not warmed"
    assert mem_cache_matching_keys("emoji_list_api:*"), "emoji list was not warmed"
    assert mem_cache_matching_keys("emoji_detail_api:*"), "emoji detail was not warmed"

    members._invalidate_member_access_caches()

    assert mem_cache_matching_keys("server_roles_api:*") == []
    assert mem_cache_matching_keys("emoji_list_api:*") == []
    assert mem_cache_matching_keys("emoji_detail_api:*") == []


def test_every_revocation_path_uses_the_invalidation_helper():
    """A new kick/ban/leave route must not forget the shared eviction call."""
    root = Path(__file__).resolve().parents[3]
    routes = root / "src" / "api" / "routes" / "servers"
    paths = {
        "kick": (routes / "members.py", "kick_member"),
        "ban": (routes / "bans.py", "ban_member"),
        "leave": (routes / "misc.py", "leave_server"),
    }
    for label, (path, funcname) in paths.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        fn = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == funcname
            ),
            None,
        )
        assert fn is not None, f"{label}: {funcname} not found"
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_invalidate_member_access_caches"
        ]
        assert calls, f"{label}: {funcname} does not invalidate access caches"
