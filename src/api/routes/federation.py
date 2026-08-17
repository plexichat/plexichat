"""
Inbound federation routes for cross-instance artifact traffic (PlexiJoin).

These routes are the receiving half of the shipped artifact federation
transport. A remote Plexichat instance forwards live artifact ops/events to
``/api/v1/federation/artifact-op`` and ``/artifact-event``; this module
authenticates each payload with the connection's shared key (HMAC-SHA256, the
same secret the outbound transport signs with), then relays the op to local
subscribers through the existing WebSocket artifact fabric.

Security model:
- The route is unauthenticated by JWT (remote instances have no local user
  token); the HMAC signature over the raw request body is the credential. A
  request is accepted only if its signature matches the shared key of an
  *active* outbound PlexiJoin connection.
- Inbound ops are relayed with ``forward_to_federation=False`` so an op never
  ping-pongs between two instances forever.
- This module never grants local permissions. A remote op is only delivered to
  local subscribers that already pass the normal ``artifact.view`` access
  check inside ``relay_artifact_op``.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

import utils.logger as logger

router = APIRouter()


def _get_plexijoin_manager():
    """Build a PlexiJoin manager for the inbound route (mirrors admin routes)."""
    import src.api as api_mod

    db = api_mod.get_db()
    if db is None:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": 503, "message": "Database not available"}},
        )

    import utils.licensing as license_module

    if not license_module.has_feature("plexijoin", default=False):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {"code": 403, "message": "PlexiJoin feature is not licensed"}
            },
        )

    try:
        import utils.encryption as enc_mod

        class _EncryptionService:
            @staticmethod
            def encrypt(data: str) -> str:
                return enc_mod.encrypt_data(data)

            @staticmethod
            def decrypt(data: str) -> str:
                return enc_mod.decrypt_data(data)

        encryption_service = _EncryptionService()
    except Exception:
        encryption_service = None

    if encryption_service is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": 503,
                    "message": "PlexiJoin encryption service unavailable",
                }
            },
        )

    from src.core.plexijoin import PlexiJoinManager

    return PlexiJoinManager(
        db=db, admin_logger=None, encryption_service=encryption_service
    )


def _authenticate(manager, body: bytes, signature: Optional[str]) -> bool:
    """Verify the HMAC signature against any active connection's shared key."""
    from src.core.artifacts.federation import verify_payload_signature

    if not signature:
        return False
    try:
        result = manager.list_connections(status="active")
        raw = result.get("connections") if isinstance(result, dict) else []
        connections = raw if isinstance(raw, list) else []
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("federation inbound: list_connections failed: %s", e)
        return False
    for conn in connections:
        if not isinstance(conn, dict):
            continue
        if verify_payload_signature(conn.get("shared_key"), body, signature):
            return True
    return False


async def _relay_op(artifact_id, op, actor_id) -> int:
    from src.api.websocket import get_dispatcher
    from src.api.websocket.artifacts import relay_artifact_op

    dispatcher = get_dispatcher()
    if dispatcher is None:
        return 0
    return await relay_artifact_op(
        dispatcher,
        int(artifact_id),
        op,
        int(actor_id) if actor_id is not None else 0,
        forward_to_federation=False,
    )


@router.post(
    "/artifact-op",
    status_code=200,
    summary="Receive a live artifact op forwarded from a remote instance",
)
async def artifact_op_inbound(request: Request):
    """Authenticate and relay a cross-instance artifact op."""
    manager = _get_plexijoin_manager()
    body = await request.body()
    signature = request.headers.get("x-plexijoin-signature")
    if not _authenticate(manager, body, signature):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    import json

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid federation payload"}},
        )

    artifact_id = payload.get("artifact_id")
    op = payload.get("op")
    actor_id = payload.get("actor_id", 0)
    if artifact_id is None or not isinstance(op, dict) or not op.get("op_type"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Malformed artifact op payload"}},
        )

    delivered = await _relay_op(artifact_id, op, actor_id)
    return {"ok": True, "delivered": delivered}


@router.post(
    "/artifact-event",
    status_code=200,
    summary="Receive an artifact-level event forwarded from a remote instance",
)
async def artifact_event_inbound(request: Request):
    """Authenticate and acknowledge a cross-instance artifact event.

    Artifact CRUD remains gated by local permissions; a remote event is an
    informational notification and is recorded, not applied to local state.
    """
    manager = _get_plexijoin_manager()
    body = await request.body()
    signature = request.headers.get("x-plexijoin-signature")
    if not _authenticate(manager, body, signature):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    import json

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid federation payload"}},
        )

    logger.info(
        "federation inbound: received artifact event %s for artifact %s",
        payload.get("event_type"),
        (payload.get("artifact") or {}).get("id")
        if isinstance(payload.get("artifact"), dict)
        else None,
    )
    return {"ok": True}
