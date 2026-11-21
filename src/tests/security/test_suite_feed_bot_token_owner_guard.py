"""Suite home feeds must not let a bot token read its owner's private data.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id lives
in ``account_id``). Every handler behind ``/suite/home/*`` and ``/suite/search``
keys its personal feed on ``current_user.user_id``:

* ``list_recent_suite_items`` / ``list_shared_suite_items`` / ``search_suite``
  build the ``_suite_feed_cache`` key ``(int(current_user.user_id), ...)``;
* all five read author-scoped artifacts/stash/sum rows (or the audit trail)
  with that same id.

The cache is a process-global dict partitioned by user id only -- unlike the
bot-natural routes, which use ``authenticated_cache_key`` to give credentials
their own namespace. Without a credential gate a bot token therefore reuses
its owner's warm cache entry and reads the owner's private Suite items.

Every other owner-scoped route family (applications, storage, features, ...)
already rejects bot credentials with ``require_user_token``. This module pins
that boundary for the Suite feed family and fails if a future handler forgets
it. Run with::

    pytest src/tests/security/test_suite_feed_bot_token_owner_guard.py -q -n 0
"""

import ast
import asyncio
import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.suite import registry as registry_routes
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType, TokenInfo

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


# Bot 500 is owned by user 100. Without the guard these handlers resolve the
# owner's identity and hit the owner's cache key / author-scoped lookups.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")

# (handler, kwargs) for every owner-scoped Suite feed handler.
_HANDLERS = [
    (registry_routes.list_recent_suite_items, {"limit": 10}),
    (registry_routes.list_pinned_suite_items, {"limit": 10}),
    (registry_routes.list_shared_suite_items, {"limit": 10}),
    (registry_routes.list_suite_activity, {"limit": 10}),
    (registry_routes.search_suite, {"q": "alpha", "limit": 10}),
]
_HANDLER_IDS = ["recent", "pinned", "shared", "activity", "search"]


class _ReachedBackend(BaseException):
    """Sentinel raised by patched backends to prove the code reached them.

    Derives from ``BaseException`` so a broad ``except Exception`` cannot turn
    a proven cache/backend lookup into a generic failure.
    """


@pytest.fixture
def backends_guarded(monkeypatch):
    """Make every post-gate lookup raise a sentinel if it is reached."""

    def _unexpected(*args, **kwargs):
        raise _ReachedBackend()

    monkeypatch.setattr(registry_routes, "_suite_feed_from_cache", _unexpected)
    monkeypatch.setattr(registry_routes, "_store_suite_feed", _unexpected)
    monkeypatch.setattr(registry_routes, "_domain_available", _unexpected)
    monkeypatch.setattr(registry_routes, "_get_manager", _unexpected)
    monkeypatch.setattr(registry_routes, "_suite_managers", _unexpected)
    monkeypatch.setattr(registry_routes, "_shared_items", _unexpected)
    monkeypatch.setattr(api, "get_db", _unexpected)
    return _unexpected


@pytest.mark.parametrize("handler,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_bot_token_refused_before_owner_cache_or_lookup(
    handler, kwargs, backends_guarded
):
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_BOT, **kwargs)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


@pytest.mark.parametrize("handler,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_user_token_reaches_owner_lookup(handler, kwargs, backends_guarded):
    """Control: a human session gets past the gate to the backend lookup.

    This proves the guard sits *before* the cache/lookup and that the runtime
    tests above are non-vacuous: without the guard the bot token would raise
    the same sentinel instead of a 403.
    """
    with pytest.raises(_ReachedBackend):
        _call(handler, current_user=_USER, **kwargs)


# ---------------------------------------------------------------------------
# Completeness: every owner-scoped Suite feed handler must carry the gate.
# ---------------------------------------------------------------------------

_REGISTRY = Path(registry_routes.__file__).resolve()


def _uses_caller_identity(node):
    return any(
        isinstance(child, ast.Attribute)
        and child.attr == "user_id"
        and isinstance(child.value, ast.Name)
        and child.value.id == "current_user"
        for child in ast.walk(node)
    )


def _has_user_token_dependency(node):
    for arg in node.args.args:
        if arg.annotation is None:
            continue
        if not any(
            isinstance(c, ast.Name) and c.id == "TokenInfo"
            for c in ast.walk(arg.annotation)
        ):
            continue
        for default in node.args.defaults:
            if not isinstance(default, ast.Call):
                continue
            if getattr(default.func, "id", None) != "Depends":
                continue
            if (
                default.args
                and getattr(default.args[0], "id", None) == "get_current_user"
            ):
                return True
    return False


def _calls_require_user_token(node):
    return any(
        isinstance(child, ast.Call)
        and getattr(child.func, "id", None) == "require_user_token"
        for child in ast.walk(node)
    )


def test_every_owner_scoped_suite_feed_handler_rejects_bot_tokens():
    tree = ast.parse(_REGISTRY.read_text(encoding="utf-8"))
    owner_scoped = set()
    omissions = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _uses_caller_identity(node) or not _has_user_token_dependency(node):
            continue
        owner_scoped.add(node.name)
        if not _calls_require_user_token(node):
            omissions.append(node.name)

    # Guard against a vacuous scan (e.g. a refactor that removes every match).
    assert {handler.__name__ for handler, _ in _HANDLERS} <= owner_scoped, (
        f"expected feed handlers missing from owner-scoped set: {sorted(owner_scoped)}"
    )
    assert not omissions, f"Suite feed handlers without require_user_token: {omissions}"
