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
every route, including read-only listing, requires the
``admin.plexijoin.manage`` permission (checked with ``check_admin_permission``
against that admin identity). Listing exposes the same federation metadata as
the admin PlexiJoin endpoints, so authentication alone must not be enough to
read it through this client-facing surface.
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

import json
import re
import inspect
from typing import Any, Dict, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.request_guards import read_body_capped
from src.core.security.hardening import (
    HardeningViolation,
    reject_control_characters,
)

import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SnowflakeIDParam

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


# --- Join payload boundaries ----------------------------------------------
#: A signed join request carries little more than a URL and a note. Reading a
#: whole JSON body unchecked let a hostile admin-session client allocate an
#: arbitrary amount of memory before any field was validated.
MAX_JOIN_BODY = 64 * 1024
MAX_NOTE_LEN = 512
MAX_MESSAGE_TYPE_LEN = 128
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_PAYLOAD_KEYS = 128
MAX_TEXT_FIELD = 2048
MAX_SELECTED_SERVERS = 100
MAX_NESTING = 8
INVITE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _bounded_text(value: Any, field: str, limit: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HardeningViolation(f"{field} must be a string")
    text = reject_control_characters(value, field)
    if len(text) > limit:
        raise HardeningViolation(f"{field} exceeds {limit} characters")
    return text


def validate_invite_code(value: Any, field: str = "invite_code") -> str:
    """Return a well-formed invite code or raise :class:`HardeningViolation`."""
    text = _bounded_text(value, field, 64)
    if not text or not INVITE_CODE_PATTERN.match(text):
        raise HardeningViolation(f"{field} has an invalid format")
    return text


def validate_join_payload(value: Any, _depth: int = 0) -> None:
    """Recursively bound a federated message payload and any invite codes."""
    if _depth > MAX_NESTING:
        raise HardeningViolation("federated payload nesting too deep")
    if isinstance(value, str):
        _bounded_text(value, "payload field", MAX_TEXT_FIELD)
        return
    if isinstance(value, dict):
        if len(value) > MAX_PAYLOAD_KEYS:
            raise HardeningViolation("federated payload has too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise HardeningViolation("federated payload key is too long")
            if "invite_code" in key:
                validate_invite_code(item, key)
            else:
                validate_join_payload(item, _depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_PAYLOAD_KEYS:
            raise HardeningViolation("federated payload sequence too long")
        for item in value:
            validate_join_payload(item, _depth + 1)


async def _read_join_json(request: Request) -> Dict[str, Any]:
    """Read and decode a bounded JSON body for a mutating /Join route."""
    try:
        if hasattr(request, "body"):
            raw = await read_body_capped(request, MAX_JOIN_BODY)
            body = json.loads(raw.decode("utf-8"))
        else:
            # Lightweight request doubles (unit tests) expose only .json();
            # the real ASGI path always goes through the capped reader above.
            result = request.json()
            body = await result if inspect.isawaitable(result) else result
    except HTTPException:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _error(400, "Invalid JSON body") from exc
    if not isinstance(body, dict):
        raise _error(400, "Body must be a JSON object")
    return body


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

    # SECURITY: a bot token carries its *owner's* ``user_id``, so mapping the
    # credential straight onto the shared admin id space would hand the bot
    # its owner's full instance-admin authority (e.g. ``admin.plexijoin.manage``
    # over the client-facing /Join surface). These routes act on a human
    # admin's authority and must only accept a real user token. Fail closed
    # before any admin lookup so a bot credential can never borrow the owner's
    # admin identity.
    if getattr(current_user, "token_type", "user") != "user":
        raise _error(403, "A user token is required for this endpoint")

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
    """Host restriction + admin identity + the federation management scope.

    ``GET /Join/list`` returns remote URLs, notes and peer identities — the
    same federation metadata the admin PlexiJoin read endpoints gate behind
    ``admin.plexijoin.manage``. Checking only the admin-identity mapping left
    an IDOR-style read on the client-facing surface: any authenticated admin
    without the federation scope could enumerate every connection. Reuse the
    full manage gate so both surfaces enforce the same authorization.
    """
    _require_manage(request, current_user)


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
        # Snowflakes as strings: a JS Number rounds 64-bit ids.
        "Join_id": str(conn["id"]),
        "id": str(conn["id"]),
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
    status: Optional[str] = Query(
        None, max_length=32, description="Filter by connection status"
    ),
    current_user: TokenInfo = Depends(get_current_user),
):
    """List federation Joins (client contract for ``GET /Join/list``).

    Auth: standard web-session user mapped to an instance admin identity with
    the ``admin.plexijoin.manage`` scope (see module docstring); host
    restriction applies, ``admin_ui.enabled`` is not required.
    """
    _require_view(request, current_user)
    try:
        try:
            status = _bounded_text(status, "status", MAX_MESSAGE_TYPE_LEN)
        except HardeningViolation as exc:
            raise _error(400, str(exc))
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
        body = await _read_join_json(request)
        remote_url = body.get("remote_url")
        if not remote_url or not isinstance(remote_url, str):
            raise _error(400, "remote_url is required")
        try:
            remote_url = cast(
                str, _bounded_text(remote_url, "remote_url", MAX_TEXT_FIELD)
            )
            note = _bounded_text(body.get("note"), "note", MAX_NOTE_LEN)
        except HardeningViolation as exc:
            raise _error(400, str(exc))

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
            # Snowflakes as strings (matches _serialize_join): a JS Number
            # rounds 64-bit ids, and the list endpoint already serves these
            # ids as strings, so a numeric id here would split the client's
            # Map keys ("1" vs 1) and break lookups.
            existing_id = str(existing["id"])
            return {
                "success": True,
                "Join_id": existing_id,
                "id": existing_id,
                "connection_id": existing_id,
                "existing": True,
            }

        connection = manager.create_connection(
            remote_instance_id=remote_instance_id,
            remote_url=remote_url,
            shared_key=manager.generate_shared_key(),
            note=note,
            admin_id=admin_id,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        delivery = await manager.send_join_request(connection["id"])
        # Snowflakes as strings (matches _serialize_join and the existing
        # path above): serving these as JSON numbers splits the client's
        # Map keys ("1" vs 1) and breaks lookups.
        created_id = str(connection["id"])
        return {
            "success": True,
            "Join_id": created_id,
            "id": created_id,
            "connection_id": created_id,
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
    join_id: SnowflakeIDParam,
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
        body = await _read_join_json(request)
        message_type = body.get("message_type")
        payload = body.get("payload")
        if not message_type or not isinstance(payload, dict):
            raise _error(400, "message_type and payload are required")
        try:
            message_type = cast(
                str, _bounded_text(message_type, "message_type", MAX_MESSAGE_TYPE_LEN)
            )
            validate_join_payload(payload)
            # SECURITY: measure the exact bytes ``_post_signed`` transmits
            # (``json.dumps(payload).encode("utf-8")``, default ensure_ascii=True).
            # A compact ``ensure_ascii=False`` measurement undercounts non-ASCII
            # text, which is escaped to ``\uXXXX`` on the wire; keep ``default=str``
            # only so near-JSON values stay measurable (it cannot shrink the form).
            if (
                len(json.dumps(payload, default=str).encode("utf-8"))
                > MAX_PAYLOAD_BYTES
            ):
                raise HardeningViolation("federated payload exceeds size limit")
        except HardeningViolation as exc:
            raise _error(400, str(exc))

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
    join_id: SnowflakeIDParam,
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
    join_id: SnowflakeIDParam,
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
        body = await _read_join_json(request)
    except JoinApiError:
        raise
    except Exception:
        body = {}
    # Contract compatibility: selected_servers accepted, currently unused.
    selected_servers = body.get("selected_servers")
    if selected_servers is not None and not isinstance(selected_servers, list):
        raise _error(400, "selected_servers must be a list")
    if selected_servers is not None and len(selected_servers) > MAX_SELECTED_SERVERS:
        raise _error(400, "selected_servers has too many entries")

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
    join_id: SnowflakeIDParam,
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
