"""Request-scoped caches shared by middleware and auth dependencies.

FastAPI caches a dependency only when the *same callable* is requested twice.
The auth/session layers are different callables that nevertheless resolve the
same facts for one request: the verified user, the client IP, whether the caller
is an internal self-test request, and the result of verifying a given token.
Resolving any of those twice means another token verification / DB lookup on
every request in the 20-50 ms band.

Everything here lives in ``scope["state"]`` (or a :class:`ContextVar` bound to
that state for callers that only have a token, such as media signing). Nothing
crosses the request boundary: :func:`isolate_request_state` gives each request
its own dict before anything is written to it, so invalidation is automatic and
no TTL is needed. That detach is load-bearing -- ASGI says the server copies its
lifespan ``state`` into every request scope, but uvicorn 0.51 passes the lifespan
dict itself, and a cache (or a ``user``) stored straight into it would then be
handed to every later request in the process.

The security checks themselves are untouched -- this only memoises their
*results* within a single request. Mocks that do not expose a real scope dict
(e.g. ``MagicMock`` in unit tests) simply bypass the cache.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

_CACHE_KEY = "_plexichat_request_context"

# Keys that hold facts about ONE request. They are never inherited from the
# server's shared lifespan state, and never written back into it.
_REQUEST_FACT_KEYS = frozenset(
    {
        _CACHE_KEY,
        "user",
        "auth_error",
        "system_error",
        "cookie_auth",
        "is_internal",
    }
)


@dataclass
class RequestContext:
    """Facts resolved once per request and reused by downstream callers."""

    user: Any = None
    user_resolved: bool = False
    ip: Any = None
    ip_resolved: bool = False
    is_self_test: Optional[bool] = None
    is_internal: Optional[bool] = None
    token_results: dict = field(default_factory=dict)


_current_context: ContextVar[Optional[RequestContext]] = ContextVar(
    "plexichat_request_context", default=None
)


def _state_of(obj: Any) -> Optional[dict]:
    """Return the mutable ASGI state dict for a Request or a raw scope.

    ``None`` means the object has no real dict behind it (a mock, or a bare
    header mapping in a helper unit test). Callers then compute without a
    cache rather than mistaking a mock attribute for a cached value.
    """
    scope = obj if isinstance(obj, dict) else getattr(obj, "scope", None)
    if not isinstance(scope, dict):
        return None
    state = scope.get("state")
    if not isinstance(state, dict):
        state = {}
        scope["state"] = state
    return state


def get_request_context(obj: Any) -> Optional[RequestContext]:
    """Get (or create) the per-request context attached to ``obj``."""
    state = _state_of(obj)
    if state is None:
        return None
    ctx = state.get(_CACHE_KEY)
    if not isinstance(ctx, RequestContext):
        ctx = RequestContext()
        state[_CACHE_KEY] = ctx
    return ctx


def isolate_request_state(obj: Any) -> None:
    """Give this request its own ``scope["state"]`` dict.

    ASGI servers are required to hand each request a *copy* of their lifespan
    state. uvicorn 0.51 hands every request the lifespan dict itself, so any
    per-request fact written to ``scope["state"]`` (the cached context, the
    resolved user, an auth error) survives the response and is then inherited
    by the next request: one login left every later anonymous request
    authenticated as that user. Detach the scope here, before this module or
    the authentication middleware writes anything, and drop request facts a
    shared dict may already be carrying. Lifespan data (database handles, client
    config) is kept, and the caller keeps writing through ``scope["state"]``.
    """
    target = obj if isinstance(obj, dict) else getattr(obj, "scope", None)
    if not isinstance(target, dict):
        return
    state = target.get("state")
    if not isinstance(state, dict):
        return
    target["state"] = {
        key: value for key, value in state.items() if key not in _REQUEST_FACT_KEYS
    }


class RequestStateMiddleware:
    """Give every request a private ``scope["state"]`` before anything runs.

    Registered outermost in :func:`src.api.app.create_app` so no inner
    middleware or route can observe (or write) the dict another request also
    holds. This is where the copy the ASGI spec assigns to the server happens,
    which is what keeps :func:`get_request_context` request-scoped on servers
    that share their lifespan state.
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        isolate_request_state(scope)
        await self.app(scope, receive, send)


def bind_current_context(obj: Any):
    """Bind this request's context to the current task; return a reset token.

    Middleware calls this once so helpers that receive only a token (and no
    Request) can still reuse the verification the middleware already did.
    """
    return _current_context.set(get_request_context(obj))


def reset_current_context(token) -> None:
    """Undo :func:`bind_current_context`."""
    _current_context.reset(token)


def current_context() -> Optional[RequestContext]:
    """Return the context bound to the current task, if any."""
    return _current_context.get()


def set_request_user(obj: Any, user: Any) -> None:
    """Store the resolved user in both the legacy state key and the cache."""
    ctx = get_request_context(obj)
    if ctx is not None:
        ctx.user = user
        ctx.user_resolved = True
    state = _state_of(obj)
    if state is not None:
        state["user"] = user


def get_request_user(obj: Any) -> Any:
    """Return the resolved user, seeding the cache from legacy state once."""
    ctx = get_request_context(obj)
    if ctx is not None and ctx.user_resolved:
        return ctx.user
    state = _state_of(obj)
    if state is not None and "user" in state:
        user = state["user"]
        if ctx is not None:
            ctx.user = user
            ctx.user_resolved = True
        return user
    return None


def cached_client_ip(obj: Any, resolver=None) -> Any:
    """Resolve the trusted-proxy client IP at most once per request."""
    ctx = get_request_context(obj)
    if ctx is not None and ctx.ip_resolved:
        return ctx.ip
    if resolver is None:
        from src.utils.net import get_client_ip as resolver

    ip = resolver(obj)
    if ctx is not None:
        ctx.ip = ip
        ctx.ip_resolved = True
    return ip


def cached_is_self_test(obj: Any, resolver=None) -> bool:
    """Run the localhost + internal-secret check at most once per request.

    A cached ``False`` is only trusted when the request carries no internal
    secret header. A cached false can otherwise be poisoned by an earlier
    middleware that evaluated the marker before the request context was fully
    bound to the current scope (e.g. the outermost rate-limit middleware): that
    stale false would suppress the self-test rate-limit bypass and admin trust
    path for the rest of the request. When the header is present we re-verify,
    which is cheap (localhost + constant-time compare) and always correct.
    """
    if resolver is None:
        import src.api as api

        resolver = api.is_self_test_request

    ctx = get_request_context(obj)
    headers = getattr(obj, "headers", None)
    has_marker = bool(
        headers is not None
        and hasattr(headers, "get")
        and headers.get("X-Plexichat-Internal-Secret")
    )
    if ctx is not None and ctx.is_self_test is not None and not has_marker:
        return ctx.is_self_test

    result = bool(resolver(obj))
    if ctx is not None:
        ctx.is_self_test = result
    return result


def cached_verify_token(obj: Any, token: str, verifier) -> Any:
    """Verify ``token`` once per request, caching success *and* failure.

    A failed verification is cached as ``None`` too: a stale bearer header that
    falls back to a cookie must not trigger a second DB-backed verification of
    the same token later in the request.
    """
    ctx = get_request_context(obj)
    if ctx is not None and token in ctx.token_results:
        return ctx.token_results[token]
    result = verifier()
    if ctx is not None:
        ctx.token_results[token] = result
    return result


def remember_token_result(token: str, result: Any, *, obj: Any = None) -> None:
    """Record a token result in the current request's cache."""
    ctx = (
        obj
        if isinstance(obj, RequestContext)
        else (get_request_context(obj) if obj is not None else current_context())
    )
    if ctx is not None:
        ctx.token_results[token] = result


def lookup_token_result(token: str, *, obj: Any = None):
    """Return ``(found, result)`` for a token in the current request's cache."""
    ctx = (
        obj
        if isinstance(obj, RequestContext)
        else (get_request_context(obj) if obj is not None else current_context())
    )
    if ctx is None or token not in ctx.token_results:
        return False, None
    return True, ctx.token_results[token]


__all__ = [
    "RequestContext",
    "bind_current_context",
    "cached_client_ip",
    "cached_is_self_test",
    "cached_verify_token",
    "current_context",
    "get_request_context",
    "get_request_user",
    "isolate_request_state",
    "RequestStateMiddleware",
    "lookup_token_result",
    "remember_token_result",
    "reset_current_context",
    "set_request_user",
]
