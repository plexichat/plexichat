"""
Shared utilities for Admin API routes.
"""

from fastapi import HTTPException, Request
from typing import Optional
from pathlib import Path
import hashlib
import html
import os
import re
import time
import utils.config as config
import utils.logger as logger
import src.api as api
from src.core.security.hardening.paths import safe_join


# Migration versions are used to select a Python module on disk in the
# migration runner (``importlib.util.spec_from_file_location``). Treat the
# version as a strict token, never as a path fragment: ``re.match`` with a
# trailing ``$`` still accepted a newline suffix (``"001\n"``), and anything
# other than exactly three ASCII digits must be rejected before it can reach
# module loading.
_MIGRATION_VERSION_RE = re.compile(r"[0-9]{3}")
_SAFE_CSP_NONCE_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MAX_ADMIN_TEMPLATE_BYTES = 2 * 1024 * 1024

# Destructive maintenance actions need a deliberate, exact confirmation
# phrase in addition to the approval gate and the elevated permission scope.
# The phrase is action-specific so a confirmation for one operation can never
# authorize a different one, and dry runs are exempt because they mutate
# nothing.
_DESTRUCTIVE_CONFIRMATIONS = {
    "migrations.rollback": "ROLLBACK MIGRATION",
    "migrations.apply": "APPLY MIGRATION",
    "migrations.apply_all": "APPLY ALL MIGRATIONS",
    "search.reindex": "REBUILD SEARCH INDEX",
}


def require_destructive_confirmation(
    action: str, confirmation: Optional[str], *, dry_run: bool = False
) -> None:
    """Fail closed unless the caller supplied the exact confirmation phrase.

    This is a second, explicit step after the single-use approval gate and
    the elevated ``maintenance`` scope: an accidental or coerced approval can
    no longer trigger an irreversible rebuild/rollback on its own.
    """
    if dry_run:
        return
    expected = _DESTRUCTIVE_CONFIRMATIONS.get(action)
    if expected is None:
        # Unknown action: never grant a destructive capability by default.
        raise HTTPException(status_code=500, detail="Unknown destructive action")
    if not isinstance(confirmation, str) or confirmation != expected:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Action '{action}' requires explicit confirmation: "
                f'send confirmation="{expected}"'
            ),
        )


def validate_migration_version(version: object) -> str:
    """Return a validated migration version or raise a 400.

    Shared by every admin route that accepts a migration version so the
    module-loading sink below the route always sees a confined value.
    """
    if not isinstance(version, str) or not _MIGRATION_VERSION_RE.fullmatch(version):
        raise HTTPException(
            status_code=400,
            detail="Invalid migration version format. Expected 3 digits (e.g., '001', '025')",
        )
    return version


def is_authenticated_self_test(request: Request) -> bool:
    """True only for the fully authenticated self-test path.

    Requires ALL of the following - any single signal alone is never
    sufficient to grant elevated treatment:

    1. Valid internal secret header (constant-time compared) ...
    2. ... from a localhost peer (both enforced by api.is_self_test_request)
    3. A populated ``request.state.user`` (i.e. the request already went
       through the authenticated self-test middleware path)
    """
    try:
        if not api.is_self_test_request(request):
            return False
    except Exception:
        return False

    user = getattr(request.state, "user", None) or request.scope.get("state", {}).get(
        "user"
    )
    return bool(user and getattr(user, "user_id", None))


# Config deep-merge re-adds default loopback hosts to an operator-narrowed
# allowlist. Cache the raw-file value so an explicit allowlist stays authoritative.
#
# ``check_host_restriction`` runs on every admin route, so the raw-file lookup
# used to ``os.stat`` config.yaml on every request even though the parsed value
# was already cached by (path, mtime, size). A short staleness ceiling throttles
# the stat itself on that hot path: within the window the last (path, mtime,
# size) key and its parsed value are reused with no filesystem access. The value
# is still version-keyed, so a real on-disk edit is observed once the ceiling
# elapses, and ``invalidate_explicit_hosts_cache`` lets admin config-reload
# callers drop it immediately.
_EXPLICIT_HOSTS_STAT_MAX_AGE_SECONDS = 5.0
_explicit_hosts_cache: dict = {"key": None, "value": None, "checked_at": 0.0}


def invalidate_explicit_hosts_cache() -> None:
    """Drop the cached explicit allowlist (admin config-reload / tests)."""
    _explicit_hosts_cache["key"] = None
    _explicit_hosts_cache["value"] = None
    _explicit_hosts_cache["checked_at"] = 0.0


def _copy_explicit_hosts(value):
    """Detach the cached allowlist so callers cannot mutate the shared gate.

    ``_explicit_hosts_cache`` is a process-wide memo keyed only by config-file
    metadata. Its ``value`` is a mutable list of allowed hosts that feeds the
    admin host-restriction gate on every admin request. Handing the cached list
    out by reference would let any caller's in-place edit (e.g. appending a
    permissive CIDR) silently rewrite the allowlist every later request from
    every user sees for the rest of the staleness window. Return a fresh list
    on every read so the cached value is never reachable by a caller.
    """
    return list(value) if isinstance(value, list) else value


def _explicit_admin_allowed_hosts():
    """Return the operator allowlist from the raw config file, or None.

    ``config.get("admin_ui")`` is post-deep-merge and always contains the
    default loopback entries. Reading the operator's own file lets an explicit
    (possibly empty) allowlist take effect as written.
    """
    try:
        instance = getattr(config, "_config_instance", None)
        path = getattr(instance, "config_path", None)
        if not path:
            return None
        now = time.monotonic()
        within_ceiling = (
            _explicit_hosts_cache["key"] is not None
            and (now - _explicit_hosts_cache["checked_at"])
            < _EXPLICIT_HOSTS_STAT_MAX_AGE_SECONDS
        )
        if within_ceiling:
            return _copy_explicit_hosts(_explicit_hosts_cache["value"])
        stat = os.stat(path)
        cache_key = (str(path), stat.st_mtime_ns, stat.st_size)
        if _explicit_hosts_cache["key"] == cache_key:
            # Nothing changed on disk: keep the parsed value, just refresh the
            # check clock so the hot path skips the stat again for a window.
            _explicit_hosts_cache["checked_at"] = now
            return _copy_explicit_hosts(_explicit_hosts_cache["value"])
        from src.utils.common_utils.utils.config.core import (
            ConfigParseError,
            MAX_CONFIG_BYTES,
            safe_load_bounded,
        )

        if str(path).lower().endswith(".json"):
            import json

            with open(path, "rb") as fh:
                raw = fh.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise ConfigParseError(
                    f"Config file exceeds maximum size ({MAX_CONFIG_BYTES} bytes)"
                )
            doc = json.loads(raw) if raw else {}
        else:
            # Bounded parse: this runs on every admin request via the host
            # gate, so an abusive config must never become a parser DoS.
            with open(path, "rb") as fh:
                doc = safe_load_bounded(fh)
        value = None
        if isinstance(doc, dict):
            ui = doc.get("admin_ui")
            if isinstance(ui, dict):
                hr = ui.get("host_restriction")
                if isinstance(hr, dict) and isinstance(hr.get("allowed_hosts"), list):
                    value = list(hr["allowed_hosts"])
        _explicit_hosts_cache["key"] = cache_key
        # Store a detached copy as well: the value parsed from the config file
        # is fresh here, but keeping the memo sealed on write means no future
        # caller can reach the stored list through the loader either.
        _explicit_hosts_cache["value"] = _copy_explicit_hosts(value)
        _explicit_hosts_cache["checked_at"] = now
        return _copy_explicit_hosts(value)
    except Exception:
        return None


def check_host_restriction(request: Request, require_admin_ui: bool = True) -> None:
    """Check if client IP is allowed to access admin surface.

    ``require_admin_ui=False`` keeps every host-level check (provisional
    license allowlist, proxy-spoofing guard, configured allowed_hosts) but
    skips the ``admin_ui.enabled`` gate, for routes that are administered
    through the normal web client by permissioned admins rather than
    through the standalone admin UI.
    """
    # Self-test/internal requests do NOT bypass host restrictions wholesale.
    # They only skip the provisional-license host allowlist, and only when
    # they arrive via the fully authenticated self-test path (internal
    # secret + localhost peer + populated request.state.user). All other
    # layers (admin UI enabled, proxy-spoofing, host allowlist) still apply.
    authenticated_self_test = is_authenticated_self_test(request)

    if not authenticated_self_test:
        # A provisional/test license is only valid when served from its
        # allowed hosts (localhost by default). Block admin access from any
        # other host so a test license can never be used to administer a
        # real, networked deployment.
        try:
            from src.utils import licensing as _licensing

            if _licensing.is_provisional():
                from src.utils.net import get_client_ip

                _client_ip = get_client_ip(request) or "unknown"
                if not _licensing.is_license_valid_for_host(_client_ip):
                    logger.warning(
                        f"Provisional test license rejected admin access from {_client_ip}"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": "Access denied"}},
                    )
        except ImportError as exc:
            # Fail closed: without the licensing module we cannot tell whether
            # this instance runs on a provisional/test license, so we cannot
            # enforce its host allowlist. Letting the request through would
            # allow a test-licensed deployment to be administered from any
            # host, defeating the guard that keeps provisional licenses off
            # networked deployments.
            logger.warning(
                "Licensing module unavailable; denying admin access: %s", exc
            )
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            ) from exc

    admin_config = config.get("admin_ui", {})
    if require_admin_ui and not admin_config.get("enabled", False):
        raise HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": "Not found"}}
        )

    from src.utils.net import get_client_ip

    client_ip = get_client_ip(request) or "unknown"

    localhost_variants = ["127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"]
    if client_ip in localhost_variants:
        direct_ip = request.client.host if request.client else "unknown"
        api_config = config.get("api", {})
        trusted_proxies = api_config.get("trusted_proxies", [])
        if direct_ip not in localhost_variants and direct_ip not in trusted_proxies:
            logger.warning(
                f"CRITICAL: Attempted admin access spoofing from {direct_ip}"
            )
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

    if authenticated_self_test:
        # Defense-in-depth fallback passed; skip only the remaining
        # host-allowlist check for this narrow, fully validated case.
        return

    host_restriction = admin_config.get("host_restriction", {})
    if host_restriction.get("enabled", True):
        allowed_hosts = host_restriction.get(
            "allowed_hosts", ["127.0.0.1", "localhost", "::1"]
        )
        # The deep merge re-adds default loopback entries to a narrowed/empty
        # list; honour the explicit operator value so the gate fails closed.
        explicit_hosts = _explicit_admin_allowed_hosts()
        if explicit_hosts is not None:
            allowed_hosts = explicit_hosts
        from src.core import admin

        if not admin.check_host_restriction(client_ip, allowed_hosts):
            logger.warning(f"Admin access denied from {client_ip}")
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )


# Several maintenance routes now stack two ``require_admin_permission``
# gates (an action scope plus ``maintenance``) on the same request. The token
# and its validation cannot change between those calls, so remember the
# verified admin id on the request and skip the duplicate session lookup.
#
# ``request.state`` is a shared ASGI state bag that every middleware and
# dependency can write to. A bare cached integer there is a privilege boundary
# by itself: anything that plants ``_pcs_verified_admin_id`` would be trusted
# as an authenticated admin without ever presenting a valid token. The value
# is therefore bound to a fingerprint of the credential that produced it, and
# is only honoured when the current request presents that same credential.
_ADMIN_ID_CACHE_ATTR = "_pcs_verified_admin_id"


def _credential_binding(request: Request):
    """Fingerprint the credential that authorises a cached admin id."""
    if is_authenticated_self_test(request):
        return ("selftest",)
    auth_header = request.headers.get("Authorization", "")
    # Request doubles (and some ASGI shims) hand back non-string header
    # values; fingerprinting them would raise out of the admin gate and turn
    # a cache lookup into a 500, so only a real bearer string is bound.
    if isinstance(auth_header, str) and auth_header.startswith("Bearer "):
        return (
            "bearer",
            hashlib.sha256(auth_header[7:].encode("utf-8")).hexdigest(),
        )
    return ("none",)


def _remember_admin_id(request: Request, admin_id: int, binding) -> None:
    state = getattr(request, "state", None)
    if state is None:
        return
    try:
        setattr(state, _ADMIN_ID_CACHE_ATTR, (binding, int(admin_id)))
    except (TypeError, ValueError, AttributeError):
        pass  # A non-state request double simply pays the lookup again.


def _cached_admin_id(request: Request, binding) -> Optional[int]:
    """Return a memoised admin id only when the credential still matches."""
    state = getattr(request, "state", None)
    cached = getattr(state, _ADMIN_ID_CACHE_ATTR, None) if state is not None else None
    if not isinstance(cached, tuple) or len(cached) != 2:
        return None
    cached_binding, cached_id = cached
    if cached_binding != binding:
        return None
    # A self-test binding is only meaningful on the fully authenticated
    # self-test path; never let one authorise an ordinary request.
    if binding == ("selftest",) and not is_authenticated_self_test(request):
        return None
    if not isinstance(cached_id, int) or isinstance(cached_id, bool) or cached_id <= 0:
        return None
    return cached_id


def get_admin_from_token(request: Request) -> int:
    """Get admin ID from Authorization header (validated once per request)."""
    binding = _credential_binding(request)
    cached = _cached_admin_id(request, binding)
    if cached is not None:
        return cached

    auth_header = request.headers.get("Authorization", "")
    has_bearer = auth_header.startswith("Bearer ")
    token = auth_header[7:] if has_bearer else ""

    state_user = request.scope.get("state", {}).get("user")
    state_attr = getattr(request.state, "user", None)
    user = state_user or state_attr

    # Token validation may ONLY be skipped for the fully authenticated
    # self-test path: valid internal secret AND localhost peer AND populated
    # request.state.user (all three checked). If user state came from
    # anything else, token validation is always performed below.
    if is_authenticated_self_test(request) and user is not None:
        from src.core.auth.permissions import has_permission

        has_admin_perm = has_permission(user.permissions, "admin.*") or has_permission(
            user.permissions, "*"
        )
        if has_admin_perm:
            # Defense-in-depth fallback: even on the trusted path require a
            # resolvable numeric identity.
            try:
                admin_id = int(user.user_id)
                _remember_admin_id(request, admin_id, binding)
                return admin_id
            except (TypeError, ValueError):
                pass  # fall through to real token validation

    if not has_bearer:
        raise HTTPException(
            status_code=401, detail={"error": {"code": 401, "message": "Invalid token"}}
        )

    from src.core import admin

    admin_id = admin.validate_session(token)
    if not admin_id:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid or expired token"}},
        )
    _remember_admin_id(request, admin_id, binding)
    return admin_id


def require_admin_permission(request: Request, permission: str) -> int:
    """
    Validate admin authentication AND check a specific permission in one call.

    A convenience wrapper that combines get_admin_from_token + check_admin_permission
    to eliminate repetitive boilerplate across admin route files.

    Args:
        request: FastAPI request object
        permission: Permission scope to check (e.g. "users.read", "admin.roles")

    Returns:
        admin_id if authenticated and authorized

    Raises:
        HTTPException 401 if not authenticated
        HTTPException 403 if permission denied
        HTTPException 500 if database unavailable
    """
    admin_id = get_admin_from_token(request)

    from src.core.admin.permissions import check_admin_permission

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, permission, db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )
    return admin_id


def _coerce_bound_id(value):
    """Normalize a target/actor id used to pin an approval, or reject it.

    Ids arrive as ints from the validated route path, but a caller may hand a
    snowflake as a decimal string. Anything else (bool, junk, oversized) is
    refused so a malformed binding can never silently degrade to "no binding"
    and fall back to the action-only behaviour.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("boolean is not an id")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError("not an id")


def require_approved_request(action_type: str, target_context=None) -> Optional[int]:
    """
    Approval gate for destructive operations.

    The action may only proceed when an unexpired APPROVED request exists in
    the ``admin_approvals`` table for ``action_type`` (see
    ``APPROVAL_REQUIRED_ACTIONS`` in core/admin/permissions.py). Single-admin
    deployments honor the existing ``single_admin_bypass`` setting because a
    lone admin can never self-approve.

    SECURITY: the approval is CONSUMED atomically as part of authorizing the
    action (single-use). The previous implementation left the approval
    reusable until its 48h expiry, so one approved ``migrations.run``
    request authorized unlimited runs.

    An approval is also bound to the exact object and actor it was granted
    for. ``target_context`` is an optional ``(target_id, acting_admin_id)``
    pair; when supplied, only an approved row whose ``target_id`` and
    ``requested_by`` match may authorize the action, and BOTH predicates are
    part of the atomic consume below (a TOCTOU gap between selection and
    consumption therefore cannot swap the target or the actor). The calling
    route has already re-checked the acting admin's execution scope, so an
    approval filed by a different admin can never be executed here — it only
    authorizes the requester who actually holds the underlying permission.
    Plain single-argument callers (global operations, direct tests) keep the
    historical action-only behaviour.

    Returns:
        The approval id that authorized the action, or None when the
        single-admin bypass applied.

    Raises:
        HTTPException 403 if no matching approved request exists.
        HTTPException 503 if the database is unavailable.
    """
    target_id = None
    acting_admin_id = None
    if target_context is not None:
        if not isinstance(target_context, (tuple, list)) or len(target_context) != 2:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Invalid approval binding"}},
            )
        try:
            target_id = _coerce_bound_id(target_context[0])
            acting_admin_id = _coerce_bound_id(target_context[1])
        except ValueError:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Invalid approval binding"}},
            )

    import src.api as api_mod

    db = api_mod.get_db()
    if db is None:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": 503, "message": "Database not available"}},
        )

    admin_config = config.get("admin_ui", {})
    approval_config = admin_config.get("approval_workflows", {})
    single_admin_bypass = approval_config.get("single_admin_bypass", True)

    now_ms = int(time.time() * 1000)

    # The admin count is only needed to decide whether the single-admin
    # bypass applies. Read it together with the candidate approval id in a
    # single round trip: two scalar subqueries always return exactly one row,
    # so this keeps the exact same decisions while saving a DB query on every
    # gated request (the approval consume below is still its own atomic step).
    # The target/actor predicates use ``? IS NULL OR col = ?`` so an unbound
    # (global) caller still matches any row while a bound caller only sees the
    # approval that was granted for that exact object and requester.
    try:
        gate_row = db.fetch_one(
            """
            SELECT
              (SELECT COUNT(*) FROM admin_users) AS admin_count,
              (SELECT id FROM admin_approvals
                WHERE action_type = ? AND status = 'approved' AND expires_at > ?
                  AND (? IS NULL OR target_id = ?)
                  AND (? IS NULL OR requested_by = ?)
                ORDER BY updated_at DESC LIMIT 1) AS approval_id
            """,
            (
                action_type,
                now_ms,
                target_id,
                target_id,
                acting_admin_id,
                acting_admin_id,
            ),
        )
    except Exception as e:
        logger.error(f"Approval gate could not read approval state: {e}")
        gate_row = None

    admin_count = None
    approval_id = None
    if gate_row is not None:
        if isinstance(gate_row, dict):
            admin_count = gate_row.get("admin_count")
            approval_id = gate_row.get("approval_id")
        else:
            admin_count = gate_row[0] if len(gate_row) > 0 else None
            approval_id = gate_row[1] if len(gate_row) > 1 else None
        try:
            admin_count = int(admin_count) if admin_count is not None else None
        except (TypeError, ValueError):
            admin_count = None

    # Single-admin bypass (existing approvals-system semantics): one admin
    # cannot self-approve, so allow direct execution when configured.
    if single_admin_bypass and admin_count == 1:
        return None

    if approval_id is None:
        logger.warning(
            f"Blocked '{action_type}': no approved request in admin_approvals"
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": (
                        f"Action '{action_type}' requires an approved request "
                        "(POST /approvals/request) before it can be executed"
                    ),
                }
            },
        )
    approval_id = int(approval_id)

    # SECURITY: single-use — atomically transition the approval to
    # 'consumed' so a second gated execution (even by another admin holding
    # the scope) cannot reuse it. The conditional UPDATE + rowcount check
    # makes the claim race-free against concurrent executions. Re-stating the
    # expiry, target and requester predicates on the authoritative write (they
    # were already on the SELECT) means a row that changed between the two
    # cannot authorize an operation it was never granted for.
    cursor = db.execute(
        """
        UPDATE admin_approvals
        SET status = 'consumed', updated_at = ?
        WHERE id = ? AND status = 'approved' AND expires_at > ?
          AND (? IS NULL OR target_id = ?)
          AND (? IS NULL OR requested_by = ?)
        """,
        (
            now_ms,
            approval_id,
            now_ms,
            target_id,
            target_id,
            acting_admin_id,
            acting_admin_id,
        ),
    )
    if getattr(cursor, "rowcount", 0) <= 0:
        logger.warning(
            f"Blocked '{action_type}': approval {approval_id} was consumed concurrently"
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": (
                        f"Action '{action_type}' requires an approved request "
                        "(POST /approvals/request) before it can be executed"
                    ),
                }
            },
        )

    # The approval row just moved to 'consumed'. The admin approval list is
    # served from a short-lived response cache, so drop it here as well —
    # otherwise a gated action could appear still 'approved' for the cache TTL.
    from src.core.admin.read_cache import invalidate_approval_caches

    invalidate_approval_caches()

    logger.info(f"'{action_type}' authorized and consumed approval {approval_id}")
    return approval_id


def load_admin_template(template_name: str, csp_nonce: Optional[str] = None) -> str:
    """Load an admin UI template.

    The template name is confined to an explicit allowlist (no filesystem
    component of a request can select a file), and every value interpolated
    into the HTML is escaped before use. A template is a code-like sink: an
    unescaped nonce can break out of its attribute and inject markup/script
    into the admin origin.
    """
    # ``migrations.html`` is served by ``admin_migrations_page``; without it in
    # this list the route answered every request with "Access Denied".
    allowed_templates = frozenset({"login.html", "dashboard.html", "migrations.html"})
    if not isinstance(template_name, str) or template_name not in allowed_templates:
        return "<h1>Access Denied</h1>"

    # Only a safe nonce shape may be stamped. Anything else (including a value
    # with quotes, angle brackets or newlines) is treated as absent so the CSP
    # fails closed instead of becoming an attribute-injection vector.
    if not isinstance(csp_nonce, str) or not _SAFE_CSP_NONCE_RE.fullmatch(csp_nonce):
        csp_nonce = None

    template_root = (
        Path(__file__).resolve().parent.parent.parent / "templates" / "admin"
    )
    try:
        template_path = Path(safe_join(str(template_root), template_name))
    except ValueError:
        return "<h1>Template Error</h1>"

    # Allowlist AND resolved-path confinement: even if the allowlist grows a
    # name with a traversal sequence, safe_join + is_relative_to keep the read
    # inside the shipped template root (no symlink or sibling-prefix escapes).
    if not template_path.is_file() or not template_path.resolve().is_relative_to(
        template_root.resolve()
    ):
        return "<h1>Template Error</h1>"

    try:
        # Bound the read so a corrupt/tampered checkout cannot turn one page
        # load into an unbounded allocation.
        if template_path.stat().st_size > _MAX_ADMIN_TEMPLATE_BYTES:
            logger.error(f"Admin template too large: {template_name}")
            return "<h1>Template Error</h1>"
        content = template_path.read_text(encoding="utf-8")
        # Stamp the running build's version into the page so the sidebar never
        # shows a stale hardcoded placeholder when dashboard.js is slow to load
        # or fails; the JS still overwrites it from /api/v1/version afterwards.
        if "{{ version }}" in content:
            from src.server.lifecycle import VERSION

            content = content.replace("{{ version }}", VERSION)
        if csp_nonce:
            # Escape even a validated nonce: it is interpolated into an HTML
            # attribute, and escaping makes the safe-by-construction property
            # explicit for future maintainers.
            safe_nonce = html.escape(csp_nonce, quote=True)
            content = content.replace("{{ csp_nonce }}", safe_nonce).replace(
                "{{ nonce }}", safe_nonce
            )

            def script_repl(match):
                tag = match.group(0)
                return (
                    tag
                    if "nonce=" in tag
                    else tag.replace("<script", f'<script nonce="{safe_nonce}"')
                )

            content = re.sub(r"<script[^>]*>", script_repl, content)
        return content
    except Exception as e:
        logger.error(f"Error reading template {template_name}: {e}")
        # Do not expose filesystem/parser exception details to browsers.
        return "<h1>Template Error</h1>"
