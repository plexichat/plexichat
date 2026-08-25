"""
Client-facing PlexiJoin federation routes (``/Join`` contract).

These routes implement the contract consumed by the web client's
``join.js`` module (GET /Join/list, POST /Join/request,
POST /Join/{id}/message|break|approve|reject) on top of the existing
``PlexiJoinManager`` operations and the admin ``/admin/plexijoin/*``
surface.

Security model: these are instance-trust operations reachable from the
normal web client session. Authentication uses the standard
authenticated-user dependency (``get_current_user``); the session user is
then mapped onto an instance admin identity via the shared id space
(``admin_users.id == user.id``, the same mapping used elsewhere, e.g.
reports moderation) and authorized through the admin permission system:
every mutating route requires the ``admin.plexijoin.manage`` permission
(checked with ``check_admin_permission`` against that admin identity),
while read-only listing requires only the admin-identity mapping.
Host restriction remains enforced and configurable (provisional-license
allowlist, proxy-spoofing guard, ``admin_ui.host_restriction``), but these
routes do NOT hard-require ``admin_ui.enabled`` — a properly-permissioned
admin calling from the web client works even when the standalone admin UI
is disabled. The PlexiJoin license gate is enforced by the shared manager
factory.

Error contract: handlers raise :class:`JoinApiError`, rendered by the
app-level exception handler as a flat ``{"error": {"code", "message"}}``
body matching what the web client parses (``body.error.message``) — never
nested under a FastAPI ``detail`` envelope.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse

router = APIRouter()


class JoinApiError(Exception):
    """Client-contract error for /Join routes.

    Rendered as a flat ``{"error": {"code", "message"}}`` JSON body by the
    app-level exception handler so the web client's ``body.error.message``
    parsing works without unwrapping FastAPI's ``detail`` envelope.
    """

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _get_manager(request):
    """Shared manager factory (license + encryption gates included)."""
    from .admin.plexijoin import _get_plexijoin_manager

    return _get_plexijoin_manager(request)


def _session_admin_id(current_user: TokenInfo) -> int:
    """Map the authenticated web-session user to an instance admin id.

    The shared id space (``admin_users.id == user.id``) is the documented
    bridge between the web-client session realm and the admin permission
    system; a session user without a matching admin identity is rejected.
    """
    if current_user is None or getattr(current_user, "user_id", None) is None:
        raise _error(401, "Authentication required")

    import src.api as api

    db = api.get_db()
    if db is None:
        raise _error(503, "Database not available")
    row = db.fetch_one(
        "SELECT id FROM admin_users WHERE id = ?", (current_user.user_id,)
    )
    if row is None:
        logger.warning(
            f"/Join access denied: user {current_user.user_id} has no admin identity"
        )
        raise _error(403, "Insufficient permissions")
    return int(row["id"])


def _require_view(request: Request, current_user: TokenInfo) -> None:
    """Host restriction (configurable) + instance-admin session for reads."""
    from .admin.utils import check_host_restriction

    check_host_restriction(request, require_admin_ui=False)
    _session_admin_id(current_user)


def _require_manage(request: Request, current_user: TokenInfo) -> int:
    """Host restriction (configurable) + admin.plexijoin.manage permission.

    The permission is resolved through the admin permission system
    (``check_admin_permission``) for the admin identity mapped from the
    authenticated web-session user.
    """
    from .admin.utils import check_host_restriction

    check_host_restriction(request, require_admin_ui=False)
    admin_id = _session_admin_id(current_user)

    import src.api as api

    db = api.get_db()
    if db is None:
        raise _error(503, "Database not available")

    from src.core.admin.permissions import check_admin_permission

    if not check_admin_permission(admin_id, "admin.plexijoin.manage", db):
        logger.warning(
            f"/Join manage access denied: admin {admin_id} lacks admin.plexijoin.manage"
        )
        raise _error(403, "Insufficient permissions")
    return admin_id


def _serialize_join(conn: Dict[str, Any]) -> Dict[str, Any]:
    """Client-contract shape: join.js keys on ``Join_id``."""
    return {
        "Join_id": conn["id"],
        "id": conn["id"],
        "remote_instance_id": conn["remote_instance_id"],
        "remote_url": conn["remote_url"],
        "status": conn["status"],
        "messages_in": conn.get("messages_in", 0),
        "messages_out": conn.get("messages_out", 0),
        "last_activity": conn.get("last_activity"),
        "note": conn.get("note"),
        "created_at": conn.get("created_at"),
    }


def _error(status_code: int, message: str) -> JoinApiError:
    return JoinApiError(status_code=status_code, message=message)


@router.get(
    "/list",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
)
async def list_joins(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by connection status"),
    current_user: TokenInfo = Depends(get_current_user),
):
    """List federation Joins (client contract for ``GET /Join/list``).

    Auth: standard web-session user mapped to an instance admin identity
    (see module docstring); host restriction applies, ``admin_ui.enabled``
    is not required.
    """
    _require_view(request, current_user)
    try:
        manager = _get_manager(request)
        result = manager.list_connections(status=status)
        return {
            "Joins": [_serialize_join(c) for c in result["connections"]],
            "connections": [_serialize_join(c) for c in result["connections"]],
            "total": result["total"],
        }
    except JoinApiError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Join list error: {e}", exc_info=True)
        raise _error(500, "Internal server error")


@router.post(
    "/request",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
)
async def request_join(
    request: Request, current_user: TokenInfo = Depends(get_current_user)
):
    """Create a pending connection to a remote instance and send it our
    signed join request (client contract for ``POST /Join/request``).

    Auth: web-session user with the ``admin.plexijoin.manage`` permission
    (see module docstring); host restriction applies.

    Body: ``{"remote_url": "...", "note": "..." (optional)}``. The shared
    key is generated locally; delivery of the signed join request is
    best-effort — if the remote is unreachable the connection stays
    ``pending`` and can be activated later via the test endpoint.
    """
    admin_id = _require_manage(request, current_user)
    try:
        body = await request.json()
        remote_url = body.get("remote_url")
        if not remote_url or not isinstance(remote_url, str):
            raise _error(400, "remote_url is required")

        from src.core.plexijoin import RemoteURLBlockedError, validate_remote_url

        try:
            validate_remote_url(remote_url)
        except RemoteURLBlockedError as e:
            raise _error(400, str(e))

        manager = _get_manager(request)

        from urllib.parse import urlparse

        host = urlparse(remote_url).netloc or remote_url
        remote_instance_id = f"remote:{host}"

        # Reuse an existing connection to the same instance instead of
        # duplicating rows on repeated requests.
        existing = manager.db.fetch_one(
            "SELECT id FROM plexijoin_connections WHERE remote_instance_id = ?",
            (remote_instance_id,),
        )
        if existing:
            return {"success": True, "Join_id": existing["id"], "existing": True}

        connection = manager.create_connection(
            remote_instance_id=remote_instance_id,
            remote_url=remote_url,
            shared_key=manager.generate_shared_key(),
            note=body.get("note"),
            admin_id=admin_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        delivery = await manager.send_join_request(connection["id"])
        return {
            "success": True,
            "Join_id": connection["id"],
            "connection_id": connection["id"],
            "delivered": bool(delivery.get("success")),
        }
    except JoinApiError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Join request error: {e}", exc_info=True)
        raise _error(500, "Internal server error")


@router.post(
    "/{join_id}/message",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Join not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
)
async def send_join_message(
    request: Request,
    join_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Send a federated message over a Join (client contract for
    ``POST /Join/{id}/message``).

    Auth: web-session user with the ``admin.plexijoin.manage`` permission
    (see module docstring); host restriction applies.

    Body: ``{"message_type": "...", "payload": {...}}``. Outbound traffic
    is recorded against the connection on success.
    """
    _require_manage(request, current_user)
    try:
        body = await request.json()
        message_type = body.get("message_type")
        payload = body.get("payload")
        if not message_type or not isinstance(payload, dict):
            raise _error(400, "message_type and payload are required")

        manager = _get_manager(request)
        result = await manager.send_message(join_id, message_type, payload)
        if not result.get("success"):
            error = result.get("error", "")
            if "not found" in error.lower():
                raise _error(404, "Join not found")
            raise _error(400, error or "Failed to send federated message")
        return {"success": True}
    except JoinApiError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Join message error: {e}", exc_info=True)
        raise _error(500, "Internal server error")


@router.post(
    "/{join_id}/break",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Join not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
)
async def break_join(
    request: Request,
    join_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Break (delete) a Join (client contract for ``POST /Join/{id}/break``).

    Auth: web-session user with the ``admin.plexijoin.manage`` permission
    (see module docstring); host restriction applies.
    """
    admin_id = _require_manage(request, current_user)
    try:
        manager = _get_manager(request)
        success = manager.delete_connection(
            connection_id=join_id,
            admin_id=admin_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        if not success:
            raise _error(404, "Join not found")
        return {"success": True}
    except JoinApiError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Join break error: {e}", exc_info=True)
        raise _error(500, "Internal server error")


@router.post(
    "/{join_id}/approve",
    responses={
        400: {"model": ErrorResponse, "description": "Request already processed"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Request not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
)
async def approve_join(
    request: Request,
    join_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Approve an inbound Join request (client contract for
    ``POST /Join/{id}/approve``).

    Auth: web-session user with the ``admin.plexijoin.manage`` permission
    (see module docstring); host restriction applies.

    Body: ``{"selected_servers": [...]}`` (accepted for contract
    compatibility; server mapping is applied when the per-server channel
    mapping layer exists).
    """
    admin_id = _require_manage(request, current_user)
    try:
        body = await request.json()
    except Exception:
        body = {}
    # Contract compatibility: selected_servers accepted, currently unused.
    selected_servers = body.get("selected_servers")
    if selected_servers is not None and not isinstance(selected_servers, list):
        raise _error(400, "selected_servers must be a list")

    try:
        manager = _get_manager(request)
        manager.approve_request(
            request_id=join_id,
            admin_id=admin_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return {"success": True}
    except ValueError as e:
        message = str(e)
        if "not found" in message.lower():
            raise _error(404, "Request not found")
        raise _error(400, message)
    except JoinApiError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Join approve error: {e}", exc_info=True)
        raise _error(500, "Internal server error")


@router.post(
    "/{join_id}/reject",
    responses={
        400: {"model": ErrorResponse, "description": "Request already processed"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Request not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
)
async def reject_join(
    request: Request,
    join_id: int,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Reject an inbound Join request (client contract for
    ``POST /Join/{id}/reject``).

    Auth: web-session user with the ``admin.plexijoin.manage`` permission
    (see module docstring); host restriction applies.
    """
    admin_id = _require_manage(request, current_user)
    try:
        manager = _get_manager(request)
        manager.deny_request(
            request_id=join_id,
            admin_id=admin_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return {"success": True}
    except ValueError as e:
        message = str(e)
        if "not found" in message.lower():
            raise _error(404, "Request not found")
        raise _error(400, message)
    except JoinApiError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Join reject error: {e}", exc_info=True)
        raise _error(500, "Internal server error")
