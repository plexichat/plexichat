"""Bot credentials must not mutate their owner's relationships.

A bot token carries the *owner's* ``user_id`` (the bot account lives in
``account_id``).  ``get_relationships`` already calls ``require_user_token``
so a bot cannot read the owner's private relationship page, but every mutation
on the same router -- sending/accepting friend requests, blocking and removing
relationships -- keyed off ``current_user.user_id`` with no credential gate.
A bot holding only a narrow permission (e.g. ``messages.send``) could therefore
friend, block and unfriend its owner's contacts.  Pin the boundary here and
fail if a future mutation forgets it.
"""

import ast
import asyncio
import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes.relationships.composer import RelationshipsRouter
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


_BOT = _token(100, token_type="bot", account_id=500)


@pytest.mark.parametrize(
    "handler, kwargs",
    [
        ("create_relationship", {"body": None}),
        ("accept_friend_request", {"user_id": "123"}),
        ("delete_relationship", {"user_id": "123"}),
        ("block_user", {"body": None}),
    ],
)
def test_bot_token_rejected_on_relationship_mutations(handler, kwargs):
    route = RelationshipsRouter()

    with pytest.raises(HTTPException) as exc:
        _call(getattr(route, handler), current_user=_BOT, **kwargs)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


_HANDLERS = {
    "friend_requests.py": {"create_relationship", "accept_friend_request"},
    "deletion.py": {"delete_relationship"},
    "blocking.py": {"block_user"},
}
_ROUTES_DIR = Path(__file__).resolve().parents[2] / "api" / "routes" / "relationships"


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


def test_every_relationship_mutation_rejects_bot_tokens():
    omissions = []
    for name, handlers in _HANDLERS.items():
        tree = ast.parse((_ROUTES_DIR / name).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in handlers:
                continue
            found.add(node.name)
            if not _calls_require_user_token(node):
                omissions.append(f"{name}:{node.name}")
        assert found == handlers, f"{name}: expected {handlers}, found {found}"

    assert not omissions, (
        "relationship mutations without require_user_token:\n  "
        + "\n  ".join(omissions)
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
