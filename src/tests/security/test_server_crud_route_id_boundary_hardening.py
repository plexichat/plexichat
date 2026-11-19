"""Server CRUD routes must parse boundary IDs with the snowflake range guard.

``get_server``, ``update_server``, ``transfer_ownership`` and ``delete_server``
used a bare ``int()`` on the ``server_id`` path parameter (and on the new-owner
id in the privileged transfer endpoint). Unlike the shared
``src.core.security.hardening.parse_id`` helper, ``int()`` accepts signs, zero,
PEP-515 underscores, Unicode digit homoglyphs and 64-bit-overflowing values.
Those malformed identifiers reached core server lookups, mutations, cache
invalidation and the ownership-transfer audit trail as if they were real
snowflakes, and distinct textual tokens (``12`` and ``1_2``) aliased to the
same row.

This suite locks the fix in both directions: a static invariant on the source
and behavioural checks that malformed ids are rejected with the route's 400
contract before any core call is made.
"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.servers import server_crud
from src.core.auth.models import TokenInfo

pytestmark = [pytest.mark.security, pytest.mark.unit]

_SOURCE = Path(__file__).resolve().parents[2] / "api/routes/servers/server_crud.py"

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


def _body(**kwargs):
    return SimpleNamespace(**kwargs)


def _is_route_handler(node) -> bool:
    return any(
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in _HTTP_METHODS
        for dec in node.decorator_list
    )


def test_server_crud_uses_parse_id_and_no_bare_int_on_boundary_params():
    source = _SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    parse_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_route_handler(node):
            continue
        str_params = {
            arg.arg
            for arg in list(node.args.posonlyargs)
            + list(node.args.args)
            + list(node.args.kwonlyargs)
            if isinstance(arg.annotation, ast.Name) and arg.annotation.id == "str"
        } & {"server_id"}
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "int"
                and sub.args
                and isinstance(sub.args[0], ast.Name)
                and sub.args[0].id in str_params
            ):
                offenders.append(f"{node.name}:{sub.lineno} {sub.args[0].id}")
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                if sub.func.id == "parse_id":
                    parse_calls += 1
    assert not offenders, f"bare int() on boundary id: {offenders}"
    assert parse_calls >= 5, f"expected parse_id on every boundary id, got {parse_calls}"
    assert "int(body.new_owner_id)" not in source


@pytest.mark.parametrize("bad", _BAD_IDS)
@pytest.mark.parametrize(
    "call",
    [
        # __wrapped__ bypasses the @cached decorator for a focused unit test.
        lambda bad: server_crud.get_server.__wrapped__(bad, _token()),
        lambda bad: server_crud.delete_server(bad, _token()),
        lambda bad: server_crud.update_server(bad, _body(), _token()),
        lambda bad: server_crud.transfer_ownership(
            bad, _body(new_owner_id=12), _token()
        ),
    ],
)
def test_malformed_server_id_rejected_before_any_core_call(call, bad):
    servers = Mock()
    with patch("src.api.get_servers", return_value=servers):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call(bad))
    assert exc.value.status_code == 400
    assert not servers.mock_calls, servers.mock_calls


@pytest.mark.parametrize("bad", _BAD_IDS)
def test_malformed_new_owner_id_rejected_before_transfer(bad):
    servers = Mock()
    with patch("src.api.get_servers", return_value=servers):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                server_crud.transfer_ownership("12", _body(new_owner_id=bad), _token())
            )
    assert exc.value.status_code == 400
    servers.transfer_ownership.assert_not_called()


def test_valid_ids_still_reach_the_core_manager():
    servers = Mock()
    servers.get_server.return_value = None
    with patch("src.api.get_servers", return_value=servers):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(server_crud.get_server.__wrapped__("123", _token()))
    assert exc.value.status_code == 404
    servers.get_server.assert_called_once_with(123, 42)
