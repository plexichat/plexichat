"""Cache invalidation for privilege and account-state mutations.

``AuthManager.verify_token`` memoises each session's ``TokenInfo`` (which
carries the user's permission map and account-state flags) for up to ten
seconds, while the user lookup helpers cache permission-bearing ``User``
objects for even longer.  A role edit, role assignment, permission grant or
direct account-lock write that skips this invalidation leaves a window in
which a still-valid bearer or CSRF-protected cookie token keeps answering with
the OLD privileges — the stale cached auth context is exactly what those
routes trust.

Call from the mutation point, after the database write succeeds, so a failed
or rolled-back change cannot clear a worker's otherwise-valid cache state.
"""

_AUTH_CACHE_PREFIXES = (
    "token_verify:*",
    "user_data:*",
    "user_by_username:*",
    "user_profile:*",
)


def invalidate_privilege_caches() -> None:
    """Drop every cached view of authentication/authorization state."""
    from src.core.database import invalidate_pattern

    for pattern in _AUTH_CACHE_PREFIXES:
        invalidate_pattern(pattern)

    # The admin role-grant cache lives in a different core package. Import it
    # lazily to avoid an auth<->admin import cycle at module load time. Every
    # admin mutation route calls this helper after its write, so routing the
    # grant-cache drop through here guarantees a revocation is visible on the
    # next delegation check even if a caller omitted the admin-side helper.
    from src.core.admin.permission_grant_cache import invalidate_grant_cache

    invalidate_grant_cache()
