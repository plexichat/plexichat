"""Server ban routes must parse boundary IDs with the snowflake range guard.

``ban_member``, ``unban_member`` and ``get_server_bans`` previously used a bare
``int()`` on the ``server_id``/``user_id`` path parameters. Unlike the shared
``src.core.security.hardening.parse_id`` helper used by every other hardened
mutating route, ``int()`` accepts signs, zero, underscore/Unicode-digit forms
and 64-bit-overflowing values. Those malformed identifiers reached the core ban
manager and its audit/event/cache side effects as if they were real snowflakes,
and distinct textual tokens (``12`` and ``1_2``) aliased to the same row.

This suite locks the fix in both directions: a static invariant on the source
and behavioural checks that malformed ids are rejected with the route's 400
contract before any core call is made.
"""

import ast
import asyncio
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from src.api.routes import servers as _servers_pkg  # noqa: F401 - import package
from src.api.routes.servers import bans as bans_routes
from src.core.auth.models import TokenInfo

pytestmark = [pytest.mark.security, pytest.mark.unit]

_BANS_SOURCE = (
    Path(__file__).resolve().parents[2] / "api/routes/servers/bans.py"
)

# Values ``int()`` accepts but a positive ASCII snowflake must not. The guard
# tolerates a leading ``+`` and surrounding whitespace (both still yield a
# plain in-range integer), so those are not in this list.
_BAD_IDS = (
    "-1",       # negative
    "0",        # zero / sentinel
    "1_2",      # underscore-separated -> 12 (PEP 515)
    "١٢",       # Unicode (Arabic-Indic) digits -> 12
    "99000000000000000000",  # beyond 2**63-1
)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def _token() -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=42,
        user_id=42,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=None,
    )


def _is_route_handler(node) -> bool:
    return any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in _HTTP_METHODS
        for dec in node.decorator_list
    )


def test_bans_module_uses_parse_id_and_no_bare_int_on_boundary_params():
    tree = ast.parse(_BANS_SOURCE.read_text(encoding="utf-8"))
    boundary_names = {"server_id", "user_id"}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_route_handler(node):
            continue
        params = {
            arg.arg
            for arg in list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
            if isinstance(arg.annotation, ast.Name) and arg.annotation.id == "str"
        } & boundary_names
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "int"
                and sub.args
                and isinstance(sub.args[0], ast.Name)
                and sub.args[0].id in params
            ):
                offenders.append(f"{node.name}:{sub.lineno} {sub.args[0].id}")
    assert not offenders, f"bare int() on boundary id: {offenders}"

    calls = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "parse_id")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "parse_id")
        )
    )
    assert calls >= 3, "expected parse_id on all three ban handlers"


@pytest.mark.parametrize("bad", _BAD_IDS)
@pytest.mark.parametrize("handler", ["ban_member", "unban_member"])
def test_mutating_ban_rejects_malformed_ids_before_core_call(handler, bad):
    servers = Mock()
    with patch("src.api.get_servers", return_value=servers):
        with pytest.raises(HTTPException) as exc:
            if handler == "ban_member":
                asyncio.run(
                    bans_routes.ban_member("12", bad, None, _token())
                )
            else:
                asyncio.run(bans_routes.unban_member("12", bad, _token()))
    assert exc.value.status_code == 400
    servers.ban_member.assert_not_called()
    servers.unban_member.assert_not_called()


@pytest.mark.parametrize("bad", _BAD_IDS)
@pytest.mark.parametrize(
    "handler", ["get_server_bans", "ban_member", "unban_member"]
)
def test_server_id_rejects_malformed_ids_before_core_call(handler, bad):
    servers = Mock()
    with patch("src.api.get_servers", return_value=servers):
        with pytest.raises(HTTPException) as exc:
            if handler == "get_server_bans":
                asyncio.run(bans_routes.get_server_bans(bad, _token()))
            elif handler == "ban_member":
                asyncio.run(
                    bans_routes.ban_member(bad, "12", None, _token())
                )
            else:
                asyncio.run(bans_routes.unban_member(bad, "12", _token()))
    assert exc.value.status_code == 400
    assert not servers.mock_calls, servers.mock_calls


def test_valid_ids_still_reach_the_core_manager():
    servers = Mock()
    servers.get_bans.return_value = []
    with patch("src.api.get_servers", return_value=servers):
        result = asyncio.run(bans_routes.get_server_bans("123", _token()))
    assert result == []
    servers.get_bans.assert_called_once_with(42, 123)
