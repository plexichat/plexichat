"""Bot credentials must not read or mutate their owner's notifications.

A bot token's ``user_id`` is its *human owner* (the bot account lives in
``account_id``).  Every handler under ``/users/@me/notifications`` reads or
writes notification rows keyed on ``current_user.user_id``.  Without a
``require_user_token`` guard a narrowly-scoped bot token could read the
owner's notification inbox, mark it read, and delete individual notifications,
purely because it shares the owner's ``user_id``.

This module pins that boundary: a bot token is rejected with 403 before any
notification service work, while a real user token still passes the guard.
"""

import ast
import asyncio
import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.routes import notifications as notifications_routes
from src.api.routes.users.helpers import require_user_token
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


# A bot token whose user_id is the owner's id.
_BOT = _token(100, token_type="bot", account_id=500)
_OWNER = _token(100)


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


@pytest.mark.parametrize(
    "handler, kwargs",
    [
        ("get_notifications", {}),
        ("mark_all_read", {}),
        ("mark_read", {"notification_id": "1"}),
        ("delete_notification", {"notification_id": "1"}),
    ],
)
def test_notification_routes_reject_bot_token_before_any_service_work(
    monkeypatch, handler, kwargs
):
    # The guard is the first statement: proving the service layer is never
    # reached makes the assertion non-vacuous (without the fix the handler
    # would fall through into api.get_notifications()).
    def _boom():
        raise AssertionError("notification service reached with a bot token")

    monkeypatch.setattr(api, "get_notifications", _boom)

    with pytest.raises(HTTPException) as exc:
        _call(getattr(notifications_routes, handler), current_user=_BOT, **kwargs)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    assert "user token" in exc.value.detail["error"]["message"].lower()


def test_guard_still_allows_a_real_user_token():
    # The guard must not blanket-ban legitimate human clients.
    assert require_user_token(_OWNER) is None


# ---------------------------------------------------------------------------
# Completeness: a new account-bound notification handler must not omit it.
# ---------------------------------------------------------------------------

_ROUTES_FILE = (
    Path(__file__).resolve().parents[2] / "api" / "routes" / "notifications.py"
)


def _uses_caller_identity(node: ast.AST) -> bool:
    """Match ``<name>.user_id`` on any local, not just ``current_user``.

    A future handler may rename the dependency parameter (``user``) or bind a
    local alias (``caller = current_user``). Keying the completeness scan on the
    literal name ``current_user`` would let such a handler key its notification
    read/write on the caller id without a guard and slip past detection. This
    deliberately errs toward false positives, which only demand a gate and
    never hide a missing one.
    """
    return any(
        isinstance(child, ast.Attribute)
        and child.attr == "user_id"
        and isinstance(child.value, ast.Name)
        for child in ast.walk(node)
    )


def _is_get_current_user_dep(call):
    return (
        isinstance(call, ast.Call)
        and getattr(call.func, "id", None) in ("Depends", "Security")
        and any(
            isinstance(a, ast.Name) and a.id == "get_current_user" for a in call.args
        )
    )


def _has_user_token_dependency(node) -> bool:
    """Match a ``TokenInfo`` dependency on ``get_current_user``.

    The annotation may be plain ``TokenInfo``, a subscript (``TokenInfo[str]``)
    or ``Annotated[TokenInfo, Depends(get_current_user)]``; the ``Depends`` may
    live in the defaults or inside the annotation. All are accepted so a future
    handler cannot dodge the scan by changing its declaration style.
    """
    for arg in node.args.args:
        if arg.annotation is None:
            continue
        if not any(
            isinstance(c, ast.Name) and c.id == "TokenInfo"
            for c in ast.walk(arg.annotation)
        ):
            continue
        if any(_is_get_current_user_dep(c) for c in ast.walk(arg.annotation)):
            return True
        if any(_is_get_current_user_dep(d) for d in node.args.defaults):
            return True
    return False


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


def _scan_account_bound(tree):
    """Return names of account-bound handlers missing ``require_user_token``."""
    omissions = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _uses_caller_identity(node) or not _has_user_token_dependency(node):
            continue
        if not _calls_require_user_token(node):
            omissions.append(node.name)
    return omissions


def test_every_account_bound_notification_handler_rejects_bot_tokens():
    omissions = _scan_account_bound(ast.parse(_ROUTES_FILE.read_text(encoding="utf-8")))

    assert not omissions, (
        "account-bound notification handlers without require_user_token:\n  "
        + "\n  ".join(omissions)
    )


def test_selector_matches_real_route_signature():
    """The completeness scan must actually select real ``Depends`` handlers."""
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


def test_ast_completeness_detector_is_non_vacuous():
    """The scan flags a real-shaped unguarded handler in every declaration style.

    This exercises the exact helpers the registry scan uses, so widening the
    matchers cannot silently turn the completeness test into a no-op.
    """

    def omissions(source):
        return _scan_account_bound(ast.parse(source))

    # Baseline: a new handler keying a notification read on the caller id,
    # with no credential gate.
    assert omissions(
        """
async def future_handler(current_user: TokenInfo = Depends(get_current_user)):
    return get_notifications(user_id=current_user.user_id)
"""
    ) == ["future_handler"]

    # The same handler with a real gate is accepted.
    assert (
        omissions(
            """
async def future_handler(current_user: TokenInfo = Depends(get_current_user)):
    require_user_token(current_user)
    return get_notifications(user_id=current_user.user_id)
"""
        )
        == []
    )

    # Renamed parameter and local alias must still be detected.
    assert omissions(
        """
async def alias_handler(current_user: TokenInfo = Depends(get_current_user)):
    caller = current_user
    return get_notifications(user_id=caller.user_id)
"""
    ) == ["alias_handler"]
    assert omissions(
        """
async def renamed_handler(user: TokenInfo = Depends(get_current_user)):
    return get_notifications(user_id=user.user_id)
"""
    ) == ["renamed_handler"]

    # Plain, subscript and Annotated TokenInfo declarations are all recognised.
    assert omissions(
        """
async def subscript_handler(current_user: TokenInfo[str] = Depends(get_current_user)):
    return get_notifications(user_id=current_user.user_id)
"""
    ) == ["subscript_handler"]
    assert omissions(
        """
async def annotated_handler(
    current_user: Annotated[TokenInfo, Depends(get_current_user)],
):
    return get_notifications(user_id=current_user.user_id)
"""
    ) == ["annotated_handler"]

    # A gate only inside a never-called nested callable is not protection.
    assert omissions(
        """
async def dead_guard_handler(current_user: TokenInfo = Depends(get_current_user)):
    def never_called():
        require_user_token(current_user)
    return get_notifications(user_id=current_user.user_id)
"""
    ) == ["dead_guard_handler"]
    assert (
        omissions(
            """
async def live_guard_handler(current_user: TokenInfo = Depends(get_current_user)):
    if current_user:
        require_user_token(current_user)
    return get_notifications(user_id=current_user.user_id)
"""
        )
        == []
    )


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
