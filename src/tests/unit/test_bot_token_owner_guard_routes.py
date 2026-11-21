"""Bot credentials must not act as their owner on application/storage routes.

A bot token's ``user_id`` is its *human owner* (the bot account lives in
``account_id``).  The route helpers under ``users/helpers.py`` reject bot
credentials on owner-scoped handlers, and the feature router already enforces
that boundary.  The application-management and storage-quota routers, however,
also key every read/write off ``current_user.user_id``.  Without the guard a
bot token could list, edit and delete its owner's applications, mint new bots,
regenerate an application's client secret, and enumerate the owner's stored
files.  This module pins that boundary and fails if a future handler forgets
it.
"""

import ast
import asyncio
import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

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
        ("applications", "list_applications", {}),
        ("applications", "create_application", {"body": None}),
        ("applications", "get_application", {"application_id": "1"}),
        (
            "applications",
            "update_application",
            {"application_id": "1", "body": None},
        ),
        ("applications", "delete_application", {"application_id": "1"}),
        ("applications", "create_bot", {"application_id": "1"}),
        ("applications", "regenerate_secret", {"application_id": "1"}),
        ("storage", "get_my_storage_quota", {}),
        ("storage", "get_my_storage_usage", {}),
        ("storage", "list_my_storage_files", {}),
    ],
)
def test_bot_token_rejected_on_owner_scoped_routes(module, handler, kwargs):
    route = importlib_import(module)

    with pytest.raises(HTTPException) as exc:
        _call(getattr(route, handler), current_user=_BOT, **kwargs)

    # The guard is the first statement, so no service stubs are needed.
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def importlib_import(module):
    import importlib

    return importlib.import_module(f"src.api.routes.{module}")


def test_guard_allows_a_real_user_token():
    from src.api.routes.users.helpers import require_user_token

    # The guard must not blanket-ban legitimate human clients.
    assert require_user_token(_OWNER) is None


# ---------------------------------------------------------------------------
# Completeness: a new owner-scoped handler must not silently omit the guard.
# ---------------------------------------------------------------------------

_ROUTES_DIR = Path(__file__).resolve().parents[2] / "api" / "routes"
_SCAN_FILES = ("applications.py", "storage.py")


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
        and getattr(default.func, "id", None) in ("Depends", "Security")
        and any(
            isinstance(arg, ast.Name) and arg.id == "get_current_user"
            for arg in default.args
        )
        for default in node.args.defaults
    )


def _calls_require_user_token(node: ast.AST) -> bool:
    """Guard calls on this handler's own execution path.

    Descend through statement-level nesting (if/for/with/try) but skip nested
    function and lambda bodies: a guard there is dead until that callable runs
    and must not mark the route as protected.
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


def test_every_owner_scoped_handler_rejects_bot_tokens():
    omissions = []
    for name in _SCAN_FILES:
        tree = ast.parse((_ROUTES_DIR / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _uses_caller_identity(node) or not _has_user_token_dependency(node):
                continue
            if not _calls_require_user_token(node):
                omissions.append(f"{name}:{node.name}")

    assert not omissions, (
        "owner-scoped handlers without require_user_token:\n  " + "\n  ".join(omissions)
    )


def test_selector_matches_real_route_signature():
    """The completeness scan must actually select plain-``TokenInfo`` handlers."""
    source = """
async def handler(current_user: TokenInfo = Depends(get_current_user)):
    require_user_token(current_user)
    return current_user.user_id
"""
    node = next(
        n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef)
    )
    assert _has_user_token_dependency(node)
    assert _uses_caller_identity(node)


def test_dead_nested_guard_does_not_protect_handler():
    """A guard only inside a never-called nested callable is not a gate."""
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
    assert not _calls_require_user_token(fns["dead_nested"])
    assert not _calls_require_user_token(fns["dead_lambda"])
    assert _calls_require_user_token(fns["live"])
