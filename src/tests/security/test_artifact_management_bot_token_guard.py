"""Artifact management must not be driven by a bot token.

A bot token's ``TokenInfo.user_id`` is its *human owner* (the bot's own id is
in ``account_id``). Every mutating artifact handler passes
``current_user.user_id`` straight into ``_authorize_scope`` -- which calls
``_require_server_permission(user_id, server_id, permission)`` and treats that
id as the acting member -- and into ``manager.create(author_id=...)``. Without
an explicit ``require_user_token`` gate, a narrow-scope bot credential therefore
wields the owner's server roles: it can create, edit and delete artifacts in
any server/conversation its owner can reach, and charge authored rows and media
cleanup to the owner's identity. This is the same owner-identity confusion
already rejected on the emoji, sticker and soundboard management surfaces.

The runtime checks below are non-vacuous: before the guard was added the bot
token reached (and would have passed) the backend permission lookup and these
tests failed; the control test proves the sentinel still fires for a real user
token. Run with::

    pytest src/tests/security/test_artifact_management_bot_token_guard.py -q -n 0
"""

import asyncio
import inspect
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from src.api.middleware.authentication import TokenInfo
from src.api.routes.artifacts.routes import crud as crud_routes
from src.api.routes.artifacts.routes import uploads as upload_routes
from src.api.schemas.artifacts import (
    ArtifactCreateRequest,
    ArtifactUpdateRequest,
    ConvertUploadRequest,
)
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={"messages.send": True},
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


# Bot 500 is owned by user 100. Without the guard every handler resolves the
# owner's identity (user 100) and wields whatever server authority that owner
# has.
_BOT = _token(100, token_type="bot", account_id=500)
_USER = _token(100, token_type="user")

_CREATE_BODY = ArtifactCreateRequest(artifact_type="file", title="x")
_UPDATE_BODY = ArtifactUpdateRequest(title="x")
_CONVERT_BODY = ConvertUploadRequest(attachment_id="5")

_HANDLERS = [
    (crud_routes.create_artifact, {"body": _CREATE_BODY}),
    (crud_routes.update_artifact, {"artifact_id": "7", "body": _UPDATE_BODY}),
    (crud_routes.delete_artifact, {"artifact_id": "7"}),
    (upload_routes._convert_upload_impl, {"body": _CONVERT_BODY}),
]
_HANDLER_IDS = [h.__name__ for h, _ in _HANDLERS]


class _ReachedBackend(BaseException):
    """Sentinel raised by a patched backend to stop the handler right there.

    Derives from ``BaseException`` so a handler's broad ``except Exception``
    cannot turn a proven backend lookup into a generic 500.
    """


@pytest.fixture
def backend_sentinel(monkeypatch):
    """Stop the handler the moment it reaches any backend lookup."""

    def _unexpected(*args, **kwargs):
        raise _ReachedBackend()

    for module in (crud_routes, upload_routes):
        for name in (
            "_validate_artifact_scope",
            "_authorize_scope",
            "_get_manager",
            "_require_artifact_capability",
            "_require_server_permission",
            "_is_conversation_member",
        ):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, _unexpected)
        if hasattr(module, "api"):
            monkeypatch.setattr(module.api, "get_db", _unexpected)
    return _unexpected


@pytest.mark.parametrize("handler,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_bot_token_refused_before_backend_lookup(handler, kwargs, backend_sentinel):
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_BOT, **kwargs)
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403


@pytest.mark.parametrize("handler,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_user_token_passes_the_identity_gate(handler, kwargs, backend_sentinel):
    """Control: a human session is not rejected by the identity gate."""
    with pytest.raises(_ReachedBackend):
        _call(handler, current_user=_USER, **kwargs)


@pytest.mark.parametrize("handler,kwargs", _HANDLERS, ids=_HANDLER_IDS)
def test_handler_source_calls_require_user_token(handler, kwargs):
    """Static guard so a future edit cannot silently drop the runtime check."""
    assert "require_user_token(current_user)" in inspect.getsource(handler)
