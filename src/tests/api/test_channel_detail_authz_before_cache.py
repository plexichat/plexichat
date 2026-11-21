"""Regression: the cached single-channel GET re-checks access on warm hits.

``_get_channel`` memoizes its rendered response per credential in the
``channel_api`` namespace. Its cold path probes channel membership /
``channels.view`` (and DM participation), but a warm ``@cached`` hit returns
from the wrapper before that body runs. A viewer whose access was revoked
(kick/ban/leave, a role change, or removal from a group DM) could otherwise
keep reading the still-warm 200 for the whole TTL. The route handler now runs
an unconditional, fail-closed access gate before the cached loader.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.api.routes.channels import crud as channel_crud
from src.core.auth.models import TokenInfo
from src.core.database import invalidate_pattern
from src.core.database.cache.base import mem_cache_matching_keys

pytestmark = [pytest.mark.unit, pytest.mark.security]

SOURCE = Path(__file__).resolve().parents[2] / "api/routes/channels/crud.py"


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _call_lines(node, name):
    lines = []
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        func = inner.func
        if (isinstance(func, ast.Name) and func.id == name) or (
            isinstance(func, ast.Attribute) and func.attr == name
        ):
            lines.append(inner.lineno)
    return sorted(lines)


def _top_level_call_lines(node, name):
    """Only an unconditional top-level call counts as a live gate."""
    lines = []
    for stmt in node.body:
        if not isinstance(stmt, ast.Expr):
            continue
        value = stmt.value
        if isinstance(value, ast.Await):  # ``await gate(...)``
            value = value.value
        if not isinstance(value, ast.Call):
            continue
        func = value.func
        if (isinstance(func, ast.Name) and func.id == name) or (
            isinstance(func, ast.Attribute) and func.attr == name
        ):
            lines.append(stmt.lineno)
    return sorted(lines)


def test_route_handler_gates_before_cached_loader():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = _find_function(tree, "_get_channel")
    assert fn is not None
    gate = _top_level_call_lines(fn, "_require_channel_access")
    loader = _call_lines(fn, "_load_channel")
    assert gate, "no unconditional access gate in the route handler"
    assert loader, "no cached loader call in the route handler"
    assert min(gate) < min(loader), "gate must run before the cached read"


def test_gate_helper_fails_closed():
    src = SOURCE.read_text(encoding="utf-8")
    helper = _find_function(ast.parse(src), "_require_channel_access")
    assert helper is not None
    segment = ast.get_source_segment(src, helper) or ""
    assert "get_servers" in segment
    assert "get_channel" in segment
    for status in ("403", "404", "500"):
        assert f'"code": {status}' in segment
    assert "if not channel" in segment
    assert "except Exception" in segment


class _DB:
    def close(self):
        pass


class _Servers:
    def __init__(self, channel=None):
        self._channel = channel
        self.get_channel_calls = 0

    def channel_exists(self, cid):
        return True

    def get_channel(self, cid, uid):
        self.get_channel_calls += 1
        return self._channel


def _token(uid=7):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=None,
    )


def _mixin():
    obj = object.__new__(channel_crud.ChannelCRUDMixin)
    obj._channel_to_response = lambda ch, uid=None: ch
    return obj


def test_warm_hit_denied_after_access_revoked():
    """A cached 200 is never served once the viewer loses channel access."""
    invalidate_pattern("channel_api:*")
    assert mem_cache_matching_keys("channel_api:*") == []

    channel = {"id": 11, "server_id": 5}
    servers = _Servers(channel=channel)

    async def run():
        with (
            patch.object(channel_crud.api, "get_servers", return_value=servers),
            patch.object(channel_crud.api, "get_db", return_value=_DB()),
            patch.object(channel_crud.api, "get_messaging", return_value=None),
        ):
            obj = _mixin()
            first = await obj._get_channel("11", _token())
            assert first == channel
            assert servers.get_channel_calls == 2

            servers._channel = None  # revoke behind the still-warm entry
            with pytest.raises(HTTPException) as exc:
                await obj._get_channel("11", _token())
            assert exc.value.status_code == 403
            assert servers.get_channel_calls == 3

    asyncio.run(run())
    invalidate_pattern("channel_api:*")


def test_gate_fails_closed_when_module_missing():
    with patch.object(channel_crud.api, "get_servers", return_value=None):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(channel_crud._require_channel_access("11", 7))
    assert exc.value.status_code == 500


def test_gate_fails_closed_when_probe_raises():
    class Broken:
        def channel_exists(self, cid):
            raise RuntimeError("db down")

    with patch.object(channel_crud.api, "get_servers", return_value=Broken()):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(channel_crud._require_channel_access("11", 7))
    assert exc.value.status_code == 500
