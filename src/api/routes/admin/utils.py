"""
Shared utilities for Admin API routes.
"""

from fastapi import HTTPException, Request
from typing import Optional
from pathlib import Path
import time
import utils.config as config
import utils.logger as logger
import src.api as api


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
        except ImportError:
            pass

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
        from src.core import admin

        if not admin.check_host_restriction(client_ip, allowed_hosts):
            logger.warning(f"Admin access denied from {client_ip}")
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )


def get_admin_from_token(request: Request) -> int:
    """Get admin ID from Authorization header."""
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
                return int(user.user_id)
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


def require_approved_request(action_type: str) -> Optional[int]:
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

    Returns:
        The approval id that authorized the action, or None when the
        single-admin bypass applied.

    Raises:
        HTTPException 403 if no approved request exists.
        HTTPException 503 if the database is unavailable.
    """
    import src.api as api_mod

    db = api_mod.get_db()
    if db is None:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": 503, "message": "Database not available"}},
        )

    admin_config = config.get("admin_ui", {})
    approval_config = admin_config.get("approval_workflows", {})

    # Single-admin bypass (existing approvals-system semantics): one admin
    # cannot self-approve, so allow direct execution when configured.
    try:
        count_row = db.fetch_one("SELECT COUNT(*) AS c FROM admin_users")
        admin_count = (
            count_row.get("c", 0) if isinstance(count_row, dict) else int(count_row[0])
        )
    except Exception as e:
        logger.error(f"Approval gate could not count admins: {e}")
        admin_count = None
    if admin_count == 1 and approval_config.get("single_admin_bypass", True):
        return None

    now_ms = int(time.time() * 1000)
    row = db.fetch_one(
        """
        SELECT id FROM admin_approvals
        WHERE action_type = ? AND status = 'approved' AND expires_at > ?
        ORDER BY updated_at DESC LIMIT 1
        """,
        (action_type, now_ms),
    )
    if row is None:
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
    approval_id = int(row["id"] if isinstance(row, dict) else row[0])

    # SECURITY: single-use — atomically transition the approval to
    # 'consumed' so a second gated execution (even by another admin holding
    # the scope) cannot reuse it. The conditional UPDATE + rowcount check
    # makes the claim race-free against concurrent executions.
    cursor = db.execute(
        """
        UPDATE admin_approvals
        SET status = 'consumed', updated_at = ?
        WHERE id = ? AND status = 'approved'
        """,
        (now_ms, approval_id),
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

    logger.info(f"'{action_type}' authorized and consumed approval {approval_id}")
    return approval_id


def load_admin_template(template_name: str, csp_nonce: Optional[str] = None) -> str:
    """Load an admin UI template."""
    allowed_templates = ["login.html", "dashboard.html", "migrations.html"]
    if template_name not in allowed_templates:
        return "<h1>Access Denied</h1>"

    template_dir = Path(__file__).parent.parent.parent / "templates" / "admin"
    template_path = (template_dir / template_name).resolve()

    if (
        not str(template_path).startswith(str(template_dir.resolve()))
        or not template_path.exists()
    ):
        return "<h1>Template Error</h1>"

    try:
        content = template_path.read_text(encoding="utf-8")
        if csp_nonce:
            import re

            content = content.replace("{{ csp_nonce }}", csp_nonce).replace(
                "{{ nonce }}", csp_nonce
            )

            def script_repl(match):
                tag = match.group(0)
                return (
                    tag
                    if "nonce=" in tag
                    else tag.replace("<script", f'<script nonce="{csp_nonce}"')
                )

            content = re.sub(r"<script[^>]*>", script_repl, content)
        return content
    except Exception as e:
        logger.error(f"Error reading template {template_name}: {e}")
        # Do not expose filesystem/parser exception details to browsers.
        return "<h1>Template Error</h1>"
