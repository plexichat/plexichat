"""Bot credentials must not drive the account session/device routes.

A bot token's ``user_id`` is its *human owner* (the bot id lives in
``account_id``).  ``auth/sessions.py`` and ``auth/devices.py`` key every
read and write off ``current_user.user_id`` -- listing sessions/devices,
revoking them, and marking a device trusted (which skips 2FA at login).
Without the ``require_user_token`` guard that every other owner-scoped
handler uses, a bot credential could therefore enumerate and tear down its
owner's sessions, rename/revoke the owner's devices, and downgrade the
owner's 2FA posture.  This module pins that boundary and fails if a future
handler forgets the guard.
"""

import ast
import asyncio
import importlib
import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.responses import Response

from src.api.middleware.authentication import TokenInfo
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# A bot token whose user_id is the owner's id.
_BOT = _token(100, token_type="bot", account_id=500)
_OWNER = _token(100)


@pytest.mark.parametrize(
    "module, handler, kwargs",
    [
        ("sessions", "logout", {"response": Response()}),
        ("sessions", "create_ws_ticket", {}),
        ("sessions", "get_sessions_list", {}),
        ("sessions", "revoke_session", {"session_id": "1"}),
        (
            "sessions",
            "revoke_all_sessions",
            {"body": None, "response": Response()},
        ),
        ("devices", "list_devices", {}),
        ("devices", "rename_device", {"device_id": "1", "body": None}),
        ("devices", "revoke_device", {"device_id": "1"}),
        ("devices", "trust_device", {"body": None}),
        ("devices", "untrust_device", {"body": None}),
    ],
)
def test_bot_token_rejected_on_owner_scoped_auth_routes(module, handler, kwargs):
    route = importlib.import_module(f"src.api.routes.auth.{module}")

    with pytest.raises(HTTPException) as exc:
        _call(getattr(route, handler), current_user=_BOT, **kwargs)

    # The guard is the first statement, so no service stubs are needed.
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_guard_allows_a_real_user_token():
    from src.api.routes.users.helpers import require_user_token

    # The guard must not blanket-ban legitimate human clients.
    assert require_user_token(_OWNER) is None


# ---------------------------------------------------------------------------
# Completeness: a new owner-scoped handler must not silently omit the guard.
# ---------------------------------------------------------------------------

_AUTH_DIR = Path(__file__).resolve().parents[2] / "api" / "routes" / "auth"
_SCAN_FILES = ("sessions.py", "devices.py")


def _uses_caller_identity(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Attribute)
        and isinstance(child.value, ast.Name)
        and child.value.id == "current_user"
        and child.attr == "user_id"
        for child in ast.walk(node)
    )


def _has_user_token_dependency(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Match ``current_user: TokenInfo = Depends(get_current_user)``.

    The real route files annotate ``current_user`` with a plain ``TokenInfo``
    (an ``ast.Name``).  An earlier version only accepted ``TokenInfo[...]``
    (an ``ast.Subscript``), so it selected zero handlers and the completeness
    check below was vacuous.  Accept both annotation shapes.
    """
    has_annotation = any(
        (isinstance(arg.annotation, ast.Name) and arg.annotation.id == "TokenInfo")
        or (
            isinstance(arg.annotation, ast.Subscript)
            and getattr(arg.annotation.value, "id", None) == "TokenInfo"
        )
        for arg in node.args.args
    )
    if not has_annotation:
        return False
    return any(
        isinstance(default, ast.Call)
        and getattr(default.func, "id", None) == "Depends"
        and any(
            isinstance(arg, ast.Name) and arg.id == "get_current_user"
            for arg in default.args
        )
        for default in node.args.defaults
    )


def _own_body_calls_require_user_token(node: ast.AST) -> bool:
    """Guard calls that execute when this handler runs.

    Descend through statement-level nesting (if/for/with/try) but skip nested
    function and lambda bodies: a guard call there is dead until that callable
    is invoked and must not mark the route as gated.
    """
    stack = [node]
    while stack:
        parent = stack.pop()
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (
                isinstance(child, ast.Call)
                and getattr(child.func, "id", None) == "require_user_token"
            ):
                return True
            stack.append(child)
    return False


def test_every_owner_scoped_auth_handler_rejects_bot_tokens():
    omissions = []
    for name in _SCAN_FILES:
        tree = ast.parse((_AUTH_DIR / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _uses_caller_identity(node) or not _has_user_token_dependency(node):
                continue
            if not _own_body_calls_require_user_token(node):
                omissions.append(f"{name}:{node.name}")

    assert not omissions, (
        "owner-scoped auth handlers without require_user_token:\n  "
        + "\n  ".join(omissions)
    )


def test_selector_matches_real_route_signature():
    """The completeness scan must actually select plain-``TokenInfo`` handlers.

    The selector used to require ``TokenInfo[...]`` and matched none of the
    real handlers, so the completeness assertion never examined anything.
    """
    source = """
async def handler(current_user: TokenInfo = Depends(get_current_user)):
    require_user_token(current_user)
    return current_user.user_id
"""
    node = next(
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.AsyncFunctionDef)
    )
    assert _has_user_token_dependency(node)
    assert _uses_caller_identity(node)


def test_dead_nested_guard_does_not_protect_handler():
    """A guard only inside a never-called nested callable is not a gate.

    The old ``ast.walk`` detector descended into nested functions and lambdas,
    so dead evidence there silenced the completeness check for an otherwise
    unguarded handler.  A live guard in statement-level nesting still counts.
    """
    source = """
async def dead_nested(current_user: TokenInfo = Depends(get_current_user)):
    def never_called():
        require_user_token(current_user)
    return current_user.user_id

async def dead_lambda(current_user: TokenInfo = Depends(get_current_user)):
    _ = lambda: require_user_token(current_user)
    return current_user.user_id

async def live(current_user: TokenInfo = Depends(get_current_user)):
    if current_user:
        require_user_token(current_user)
    return current_user.user_id
"""
    fns = {
        n.name: n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.AsyncFunctionDef)
    }
    assert not _own_body_calls_require_user_token(fns["dead_nested"])
    assert not _own_body_calls_require_user_token(fns["dead_lambda"])
    assert _own_body_calls_require_user_token(fns["live"])
