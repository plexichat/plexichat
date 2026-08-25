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

from typing import Any, Dict, Optional

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


def _authenticate(
    manager, body: bytes, signature: Optional[str], statuses=("active",)
) -> Optional[Dict[str, Any]]:
    """Verify the HMAC signature against a connection's shared key.

    Returns the matching connection dict (so callers can attribute traffic),
    or ``None`` when no known connection's key verifies the payload.
    """
    from src.core.artifacts.federation import verify_payload_signature

    if not signature:
        return None
    for status in statuses:
        try:
            result = manager.list_connections(status=status)
            raw = result.get("connections") if isinstance(result, dict) else []
            connections = raw if isinstance(raw, list) else []
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("federation inbound: list_connections failed: %s", e)
            continue
        for conn in connections:
            if not isinstance(conn, dict):
                continue
            if verify_payload_signature(conn.get("shared_key"), body, signature):
                return conn
    return None


def _parse_json_body(body: bytes) -> Dict[str, Any]:
    import json

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid federation payload"}},
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid federation payload"}},
        )
    return payload


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

    payload = _parse_json_body(body)

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

    payload = _parse_json_body(body)

    logger.info(
        "federation inbound: received artifact event %s for artifact %s",
        payload.get("event_type"),
        (payload.get("artifact") or {}).get("id")
        if isinstance(payload.get("artifact"), dict)
        else None,
    )
    return {"ok": True}


@router.post(
    "/join-request",
    status_code=200,
    summary="Receive a federation join request from a remote instance",
)
async def join_request_inbound(request: Request):
    """Record an inbound PlexiJoin join request for admin review.

    The request must be HMAC-signed with the shared key of a connection this
    instance already knows about (pending or active) — that key exchange is
    what bootstraps trust. Verified requests are inserted into
    ``plexijoin_inbound_requests`` as ``pending`` and surface through the
    admin review endpoints.
    """
    manager = _get_plexijoin_manager()
    body = await request.body()
    signature = request.headers.get("x-plexijoin-signature")
    conn = _authenticate(manager, body, signature, statuses=("active", "pending"))
    if not conn:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    payload = _parse_json_body(body)

    remote_instance_id = str(payload.get("instance_id") or "").strip()
    if not remote_instance_id:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "instance_id is required"}},
        )

    row = manager.submit_inbound_request(
        remote_instance_id=remote_instance_id,
        remote_url=str(payload.get("remote_url") or conn.get("remote_url") or ""),
        requested_by=payload.get("requested_by"),
        note=payload.get("note"),
    )
    return {"ok": True, "request_id": row["id"]}


@router.post(
    "/message",
    status_code=200,
    summary="Receive a federated message from a remote instance",
)
async def message_inbound(request: Request):
    """Receive a cross-instance message over an ACTIVE federation link.

    The payload is HMAC-verified against the sending connection's shared
    key; verified traffic is recorded via ``record_traffic`` so the inbound
    counters stay accurate. Delivery into local channels is limited to the
    logged relay until the per-server channel-mapping layer exists — the
    message is acknowledged, accounted, and logged.
    """
    manager = _get_plexijoin_manager()
    body = await request.body()
    signature = request.headers.get("x-plexijoin-signature")
    conn = _authenticate(manager, body, signature, statuses=("active",))
    if not conn:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Invalid federation signature"}},
        )

    payload = _parse_json_body(body)
    if not payload.get("message_type"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "message_type is required"}},
        )

    try:
        manager.record_traffic(int(conn["id"]), "inbound", 1)
    except Exception as e:  # pragma: no cover - defensive accounting
        logger.debug("federation inbound: record_traffic failed: %s", e)

    logger.info(
        "federation inbound: %s message over connection %s from %s",
        payload.get("message_type"),
        conn.get("id"),
        conn.get("remote_instance_id"),
    )
    return {"ok": True}
