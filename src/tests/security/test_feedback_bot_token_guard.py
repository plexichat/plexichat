"""Feedback routes must not let a bot token spend its owner's quota.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id
lives in ``account_id``). Both handlers in ``routes/feedback.py`` key off that
id:

* ``submit_feedback`` charges ``POST /feedback`` to
  ``acheck_rate_limit(user_id=current_user.user_id, ...)``;
* ``get_feedback_status`` reads the very same per-account bucket via
  ``manager._generate_bucket_key(..., user_id=current_user.user_id)``.

Without an identity gate a leaked bot token drains the owner's feedback quota,
evades its own bucket, and discloses how much of the owner's quota remains.
The report-submission and user-discovery routers already reject bots on this
exact pattern; these two handlers were the remaining gap.

The runtime checks are non-vacuous: a sentinel patched into the rate limiter
records any lookup that happens *after* the gate, so removing the guard makes
the bot-token case reach the sentinel and fail. The AST check mirrors the same
boundary for future handlers and is itself proven non-vacuous against a
synthetic unguarded handler.

Run with::

    pytest src/tests/security/test_feedback_bot_token_guard.py -q -n 0
"""

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes import feedback as feedback_routes
from src.api.schemas.common import SnowflakeID
from src.api.schemas.feedback import FeedbackCreate
from src.core.auth.models import AccountType

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="bot" if token_type == "bot" else "standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


# Bot 500 is owned by user 100. Without the guard the handlers resolve the
# owner's identity and charge/read user 100's POST /feedback bucket.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")
_BODY = FeedbackCreate(content="Please fix the thing that broke today")
_REQUEST = SimpleNamespace(state=SimpleNamespace(is_internal=False))


class _ReachedBucket(BaseException):
    """Sentinel raised by the patched rate limiter to stop the handler here.

    Derives from ``BaseException`` (not ``Exception``) so the handlers' broad
    ``except Exception`` rewrites cannot swallow it and turn the proof of
    reaching the bucket into a generic 500.
    """


@pytest.fixture
def bucket(monkeypatch):
    """Record every rate-limit lookup, then stop the handler right there."""
    calls = []

    def _record(*args, **kwargs):
        uid = kwargs.get("user_id", args[0] if args else None)
        calls.append(uid)
        raise _ReachedBucket()

    async def _arecord(*args, **kwargs):
        uid = kwargs.get("user_id", args[0] if args else None)
        calls.append(uid)
        raise _ReachedBucket()

    class _FakeManager:
        def _generate_bucket_key(self, *args, **kwargs):
            calls.append(kwargs.get("user_id"))
            return "sentinel-bucket-key"

    async def _abucket_info(bucket_key):
        raise _ReachedBucket()

    monkeypatch.setattr(feedback_routes.ratelimit, "acheck_rate_limit", _arecord)
    monkeypatch.setattr(
        feedback_routes.ratelimit, "get_manager", lambda: _FakeManager()
    )
    monkeypatch.setattr(feedback_routes.ratelimit, "aget_bucket_info", _abucket_info)
    # Keep the pre-rate-limit "enabled" check from short-circuiting the submit
    # handler in the control case.
    real_get = feedback_routes.config.get

    def _config_get(key, default=None):
        if key == "feedback.enabled":
            return True
        return real_get(key, default)

    monkeypatch.setattr(feedback_routes.config, "get", _config_get)
    return calls


_CASES = [
    (feedback_routes.submit_feedback, {"feedback_data": _BODY, "request": _REQUEST}),
    (feedback_routes.get_feedback_status, {}),
]
_IDS = ["submit", "status"]


@pytest.mark.parametrize("handler,kwargs", _CASES, ids=_IDS)
def test_bot_token_refused_before_rate_limit_lookup(handler, kwargs, bucket):
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_BOT, **kwargs)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    # The owner's POST /feedback bucket was never touched.
    assert bucket == []


@pytest.mark.parametrize("handler,kwargs", _CASES, ids=_IDS)
def test_user_token_still_reaches_rate_limit_lookup(handler, kwargs, bucket):
    """Control: a human session still reaches the per-account rate limiter."""
    with pytest.raises(_ReachedBucket):
        _call(handler, current_user=_USER, **kwargs)
    assert bucket == [SnowflakeID("100")]


# ---------------------------------------------------------------------------
# Completeness: a future feedback handler must not silently omit the gate.
# ---------------------------------------------------------------------------

_FEEDBACK_ROUTE = Path(feedback_routes.__file__)


def _has_user_token_dependency(node):
    """Match ``current_user: TokenInfo = Depends(get_current_user)``.

    Deliberately matches a plain ``TokenInfo`` annotation *and* a
    ``TokenInfo[...]`` subscript: several older completeness guards only
    looked for the subscript and were vacuous against real route signatures.
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


def _uses_caller_user_id(node):
    return any(
        isinstance(child, ast.Attribute)
        and child.attr == "user_id"
        and isinstance(child.value, ast.Name)
        for child in ast.walk(node)
    )


def _calls_require_user_token(node):
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


def _omissions(tree):
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _uses_caller_user_id(node) or not _has_user_token_dependency(node):
            continue
        if not _calls_require_user_token(node):
            out.append(node.name)
    return out


def test_every_feedback_handler_gates_bot_tokens():
    tree = ast.parse(_FEEDBACK_ROUTE.read_text(encoding="utf-8"))
    assert not _omissions(tree), (
        "feedback handlers using current_user.user_id without "
        "require_user_token: " + ", ".join(_omissions(tree))
    )


def test_ast_completeness_detector_is_non_vacuous():
    """The scan must actually flag a real-shaped unguarded handler."""
    source = """
async def future_handler(
    current_user: TokenInfo = Depends(get_current_user),
):
    return current_user.user_id
"""
    assert _omissions(ast.parse(source)) == ["future_handler"]
    source_with_guard = """
async def future_handler(
    current_user: TokenInfo = Depends(get_current_user),
):
    require_user_token(current_user)
    return current_user.user_id
"""
    assert _omissions(ast.parse(source_with_guard)) == []


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
