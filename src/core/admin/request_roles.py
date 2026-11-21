"""Request-scoped cache of the roles resolved during admin session validation.

Every authenticated admin request performs two reads before it reaches its
handler: :meth:`SessionMixin.validate_session` looks up the bearer token in
``admin_sessions``, then :func:`src.core.admin.permissions.check_permission`
fetches the caller's roles from ``admin_roles`` to check the route scope. The
two reads are independent only on paper -- the role rows are fixed for the
lifetime of a request, and the session row already carries the admin id that
selects them.

``validate_session`` now selects the role permission payloads in the same
statement as the session and stores them here. ``check_permission`` consumes
them for the rest of that request, turning the auth preamble from two round
trips into one. This matters for the hot admin read endpoints (admin-users,
role listings, approvals, migrations, audit export), where the preamble was a
fixed tax on every call.

Values live in a :class:`contextvars.ContextVar`, which is bound to the current
task/context rather than the process, so one request's roles can never be seen
by another request. An identity guard (db object + admin id) additionally
rejects a value captured for a different caller, e.g. a direct helper call in
a test or a validation performed for a different admin earlier in the same
request. Missing values simply fall back to the original role query, so the
authenticated self-test path -- which resolves identity from request state
without a session lookup -- behaves exactly as before.
"""

from contextvars import ContextVar
from typing import Any, Optional, Tuple


def _bound_request_context():
    """The middleware's per-request context, when one is bound.

    Importing lazily keeps ``core.admin`` independent of the API layer at
    module import time. The middleware binds/resets a fresh context object for
    every request, so recording its identity makes a cached value from a
    previous request fail closed even on an unusual server that reuses a task.
    """
    try:
        from src.api.middleware.request_context import current_context

        return current_context()
    except Exception:
        return None


# (db identity, admin id, role permission payloads, bound request context)
_CachedRoles = Tuple[int, int, Tuple[Any, ...], Any]
_session_roles: ContextVar[Optional[_CachedRoles]] = ContextVar(
    "admin_session_roles", default=None
)

# Parsed (dict) form of the role permission payloads, memoised for the rest of
# the request. ``check_permission`` must turn the raw JSON blobs into dicts
# before it can decide a grant. When a request stacks several permission gates
# (e.g. the maintenance routes that check an action scope *and* ``maintenance``)
# the same blobs were re-parsed on every gate. This cache holds the parsed form
# behind the exact same identity guard as the raw payloads, so the JSON is
# decoded once per request and a different admin/db/request can never observe
# another caller's parsed grants. It is cleared on re-validation and by
# ``clear_session_roles`` (which privilege mutations invoke).
_session_parsed_roles: ContextVar[Optional[_CachedRoles]] = ContextVar(
    "admin_session_parsed_roles", default=None
)

# Fingerprint of the admin session credential that was validated for this
# request. It is a one-way hash (never the raw bearer value) and is only used
# to partition the short-lived role-grant cache by caller. ``None`` means no
# session was validated (direct core call / self-test identity path).
_session_credential: ContextVar[Optional[str]] = ContextVar(
    "admin_session_credential", default=None
)


def remember_session_credential(credential: Optional[str]) -> None:
    """Bind the validated admin session fingerprint to the current request."""
    _session_credential.set(credential)


def current_session_credential() -> Optional[str]:
    """Return the current request's admin session fingerprint, if any."""
    return _session_credential.get()


def remember_session_roles(db, admin_id: int, permission_payloads) -> None:
    """Store the role payloads resolved alongside a successful session lookup."""
    _session_roles.set(
        (id(db), admin_id, tuple(permission_payloads), _bound_request_context())
    )
    # A fresh validation supersedes any parsed form derived from older raw
    # payloads; the next permission check will decode the new blobs once.
    _session_parsed_roles.set(None)


def cached_session_roles(db, admin_id: int) -> Optional[Tuple[Any, ...]]:
    """Return the cached role payloads for ``admin_id`` on ``db``, if any.

    Returns ``None`` when no session was validated in this request or when the
    cached value belongs to a different admin/database; callers then fall back
    to the ordinary role lookup.
    """
    value = _session_roles.get()
    if value is None:
        return None
    db_identity, cached_admin_id, payloads, bound_context = value
    if bound_context is not None and bound_context is not _bound_request_context():
        # A different request is running in this task (or the middleware reset
        # the binding); never reuse the previous request's roles.
        return None
    if db_identity != id(db):
        return None
    if cached_admin_id == admin_id:
        return payloads
    # Some drivers hand back snowflake ids as text. The token validator and
    # the permission gate normally receive the identical value, but tolerate a
    # int/str difference without ever matching two genuinely different ids.
    try:
        if int(cached_admin_id) == int(admin_id):
            return payloads
    except (TypeError, ValueError):
        pass
    return None


def remember_parsed_session_roles(db, admin_id: int, parsed_payloads) -> None:
    """Store the parsed (dict) role payloads for the current request."""
    _session_parsed_roles.set(
        (id(db), admin_id, tuple(parsed_payloads), _bound_request_context())
    )


def cached_parsed_session_roles(db, admin_id: int) -> Optional[Tuple[Any, ...]]:
    """Return parsed role payloads recorded for ``admin_id`` on ``db``, if any.

    The returned dicts are request-private and treated as immutable by the
    permission gate; ``None`` means the caller must parse the raw payloads.
    The identity gate mirrors ``cached_session_roles`` verbatim.
    """
    value = _session_parsed_roles.get()
    if value is None:
        return None
    db_identity, cached_admin_id, payloads, bound_context = value
    if bound_context is not None and bound_context is not _bound_request_context():
        return None
    if db_identity != id(db):
        return None
    if cached_admin_id == admin_id:
        return payloads
    try:
        if int(cached_admin_id) == int(admin_id):
            return payloads
    except (TypeError, ValueError):
        pass
    return None


def clear_session_roles() -> None:
    """Drop the cached roles (used by tests and request-boundary tooling)."""
    _session_roles.set(None)
    _session_parsed_roles.set(None)
    _session_credential.set(None)
