"""A bot credential must be rate-limited as the bot, not as its human owner.

``AuthManager._verify_bot_token`` stores the bot's OWNER in ``TokenInfo.user_id``
and the bot's own account in ``account_id``. Every rate-limit extraction site
used to key its bucket on ``user_id``, so a bot token landed on its owner's
global/user quota: it could exhaust the owner's budget and evade its own while
``is_bot`` only tweaked the route multiplier. That is the same owner-principal
confusion the gateway (``ConnectionHandler._verify_token``) and the REST routes
(``require_user_token``) already reject.

The runtime tests below are non-vacuous: before the fix the bot token reached
(and recorded) the OWNER id in the bucket lookup, so each assertion below failed.
Control tests prove a legitimate user token still reaches its own bucket.

Run with::

    pytest src/tests/ratelimit/test_bot_token_owner_rate_limit_principal.py -q -n 0
"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from src.api.middleware.rate_limiting import get_user_info_from_request
from src.core.auth.models import AccountType, TokenInfo
from src.core.ratelimit.manager.principals import normalise_principal

pytestmark = pytest.mark.unit

OWNER_ID = 100
BOT_ID = 900
OTHER_BOT_ID = 901
_IP = "203.0.113.5"
_UNSET = object()


def _token(*, bot: bool, account_id=_UNSET, user_id=OWNER_ID) -> TokenInfo:
    if account_id is _UNSET:
        account_id = BOT_ID if bot else user_id
    return TokenInfo(
        valid=True,
        token_type="bot" if bot else "user",
        account_id=account_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="bot" if bot else "standard",
        expires_at=None,
        username="bot" if bot else "owner",
        account_type=AccountType.BOT if bot else AccountType.USER,
    )


def _request(user) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/users/@me",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": (_IP, 12345),
        "state": {"user": user},
    }
    return Request(scope)


# --- configured getter (the app's primary extraction site) -----------------


def test_bot_credential_is_charged_to_bot_account_not_owner():
    info = get_user_info_from_request(_request(_token(bot=True)))

    assert info["user_id"] == BOT_ID
    assert info["is_bot"] is True
    # Pre-fix this was OWNER_ID: the bot spent the owner's quota.
    assert info["user_id"] != OWNER_ID


def test_user_credential_keeps_its_own_id():
    info = get_user_info_from_request(_request(_token(bot=False)))

    assert info["user_id"] == OWNER_ID
    assert info["is_bot"] is False


@pytest.mark.parametrize(
    "account_id,user_id",
    [
        (None, OWNER_ID),  # missing bot account
        (0, OWNER_ID),  # unusable bot account
        (OWNER_ID, OWNER_ID),  # not a distinct bot account
        ("not-a-number", OWNER_ID),  # malformed bot account
    ],
)
def test_malformed_bot_verdict_never_falls_back_to_owner(account_id, user_id):
    info = get_user_info_from_request(
        _request(_token(bot=True, account_id=account_id, user_id=user_id))
    )

    assert info["is_bot"] is True
    # Fail closed: no owner id, so the limiter keys on the trusted IP instead.
    assert info["user_id"] is None


# --- end-to-end middleware proof: which bucket principal is actually used ---


@pytest.mark.asyncio
async def test_middleware_charges_bot_bucket_to_bot_account(monkeypatch):
    import src.api.middleware.rate_limiting as rl_mw
    import src.core.ratelimit as rl

    monkeypatch.setattr(rl_mw, "cached_client_ip", lambda *_a, **_kw: _IP)
    monkeypatch.setattr(rl, "is_setup", lambda: True)

    seen = []

    async def _record(**kwargs):
        seen.append(kwargs["user_id"])
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(rl, "acheck_rate_limit", _record)

    from src.core.ratelimit.middleware import RateLimitMiddlewareASGI

    async def _noop_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RateLimitMiddlewareASGI(
        app=_noop_app,
        get_user_info=get_user_info_from_request,
        include_headers_on_success=False,
    )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    bot_request = _request(_token(bot=True))
    await middleware(bot_request.scope, receive, send)
    # Pre-fix: the bot token reached the owner-scoped bucket (OWNER_ID).
    assert seen[-1] == BOT_ID

    user_request = _request(_token(bot=False))
    await middleware(user_request.scope, receive, send)
    assert seen[-1] == OWNER_ID


# --- no cross-bot collision, and the decorator path agrees -----------------


def test_bots_of_same_owner_get_distinct_bucket_principals():
    bot_a = get_user_info_from_request(_request(_token(bot=True, account_id=BOT_ID)))[
        "user_id"
    ]
    bot_b = get_user_info_from_request(
        _request(_token(bot=True, account_id=OTHER_BOT_ID))
    )["user_id"]
    owner = get_user_info_from_request(_request(_token(bot=False)))["user_id"]

    assert bot_a == BOT_ID
    assert bot_b == OTHER_BOT_ID
    assert owner == OWNER_ID
    # The canonical keys fed to the backend must be distinct, not collapsed
    # onto the shared human owner.
    assert normalise_principal(bot_a, None)[0] != normalise_principal(bot_b, None)[0]
    assert normalise_principal(bot_a, None)[0] != normalise_principal(owner, None)[0]


@pytest.mark.asyncio
async def test_decorator_rate_limit_charges_bot_account(monkeypatch):
    import src.core.ratelimit as rl
    from src.core.ratelimit.decorators import rate_limit

    monkeypatch.setattr(rl, "is_setup", lambda: True)
    seen = []

    async def _record(**kwargs):
        seen.append(kwargs["user_id"])
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(rl, "acheck_rate_limit", _record)

    @rate_limit(requests=5, window_seconds=60)
    async def handler(request):
        return "ok"

    await handler(_request(_token(bot=True)))
    assert seen == [BOT_ID]


@pytest.mark.asyncio
async def test_core_middleware_default_getter_charges_bot_account(monkeypatch):
    """The core ASGI fallback (no configured getter) agrees with the app getter."""
    import src.core.ratelimit as rl
    from src.core.ratelimit.middleware import RateLimitMiddlewareASGI

    monkeypatch.setattr(rl, "is_setup", lambda: True)
    seen = []

    async def _record(**kwargs):
        seen.append(kwargs["user_id"])
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(rl, "acheck_rate_limit", _record)

    captured = {}

    async def _capture(scope, receive, send):
        # The default getter runs inside the middleware; re-derive from the
        # same scope to assert the identity it resolved.
        captured["user"] = scope["state"]["user"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RateLimitMiddlewareASGI(app=_capture, include_headers_on_success=False)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    req = _request(_token(bot=True))
    # Default path extracts the IP via get_client_ip(request); the socket peer
    # in the scope is a trusted, bounded value for this test.
    await middleware(req.scope, receive, send)

    assert seen[-1] == BOT_ID
    assert captured["user"] is req.scope["state"]["user"]
