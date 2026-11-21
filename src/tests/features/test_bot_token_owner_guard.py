"""Bot credentials must never drive the user-scoped feature routes.

A bot token's ``user_id`` is its *owner* (the bot id lives in ``account_id``).
Every ``/@me`` feature route -- and every route that uses ``current_user.user_id``
as the actor for personal state -- therefore used to read or mutate the owner's
last chat, bookmarks, push tokens, scheduled messages, profile and audit logs
when presented with a bot token. The user router already rejects bot tokens on
every owner-scoped endpoint; this module pins the same boundary for the feature
router and fails if a future handler forgets the guard.
"""

import ast
import asyncio
import importlib
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
        ("last_chat", "save_last_chat", {"body": None}),
        ("last_chat", "get_last_chat", {}),
        ("last_chat", "get_recent_chats", {}),
        ("bookmarks", "add_bookmark", {"body": None}),
        ("bookmarks", "remove_bookmark", {"message_id": "1"}),
        ("bookmarks", "list_bookmarks", {}),
        ("scheduled_messages", "create_scheduled_message", {"body": None}),
        ("scheduled_messages", "list_scheduled_messages", {}),
        ("scheduled_messages", "cancel_scheduled_message", {"scheduled_id": "1"}),
        ("push", "register_push_token", {"body": None}),
        ("push", "list_push_tokens", {}),
        ("push", "unregister_push_token_by_id", {"token_id": "1"}),
        ("push", "unregister_push_token", {"token_value": "t"}),
        ("audit_logs", "get_user_audit_logs", {}),
        ("forwarding", "forward_message", {"body": None}),
        ("voice", "send_voice_message", {"body": None}),
        ("reports", "submit_enhanced_report", {"body": None}),
        ("reports", "get_report_details", {"report_id": 1}),
        ("tier_features", "get_my_features", {}),
        ("slowmode", "get_channel_slowmode", {"channel_id": "1"}),
        ("onboarding", "apply_onboarding_preset", {"body": None}),
    ],
)
def test_bot_token_rejected_on_user_scoped_feature_routes(module, handler, kwargs):
    route = importlib.import_module(f"src.api.routes.features.{module}")

    with pytest.raises(HTTPException) as exc:
        _call(getattr(route, handler), current_user=_BOT, **kwargs)

    # The guard is the first statement, so no service/module stubs are needed.
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


def test_update_own_profile_rejects_bot_token():
    from src.api.routes.features import profiles as profiles_route

    with pytest.raises(HTTPException) as exc:
        _call(
            profiles_route.update_own_profile,
            body=profiles_route.ProfileUpdateRequest(),
            current_user=_BOT,
        )
    assert exc.value.status_code == 403


def test_guard_allows_a_real_user_token():
    from src.api.routes.users.helpers import require_user_token

    # No exception for a genuine user session -- the guard must not blanket-ban
    # legitimate human clients from the feature routes.
    assert require_user_token(_OWNER) is None


# ---------------------------------------------------------------------------
# Completeness: a new owner-scoped handler must not silently omit the guard.
# ---------------------------------------------------------------------------

_FEATURES_DIR = Path(__file__).resolve().parents[2] / "api" / "routes" / "features"

# Public read with explicit per-visibility projection, and a moderator route
# gated by the admin_users table; neither acts on the caller's own record.
_SCAN_EXEMPTIONS = frozenset({"get_user_profile", "update_report_status"})


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


def test_every_caller_scoped_feature_handler_rejects_bot_tokens():
    omissions = []
    for path in sorted(_FEATURES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in _SCAN_EXEMPTIONS:
                continue
            if not _uses_caller_identity(node) or not _has_user_token_dependency(node):
                continue
            if not _calls_require_user_token(node):
                omissions.append(f"{path.name}:{node.name}")

    assert not omissions, (
        "owner-scoped feature handlers without require_user_token:\n  "
        + "\n  ".join(omissions)
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
