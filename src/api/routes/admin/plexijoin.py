"""
Admin PlexiJoin federation routes for managing inter-instance connections.
"""

from fastapi import APIRouter, Request, HTTPException, Query
from typing import Optional

from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger

router = APIRouter()


def _get_plexijoin_manager(request):
    """Get PlexiJoin manager instance."""
    import src.api as api_mod
    from src.core.plexijoin import PlexiJoinManager
    from src.core.admin.logging import get_admin_logger
    from utils import licensing as license_module  # type: ignore[import]

    db = api_mod.get_db()
    if db is None:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": 503, "message": "Database not available"}},
        )
    admin_logger = get_admin_logger()

    if not license_module.has_feature("plexijoin", default=False):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "PlexiJoin feature is not licensed",
                }
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
                    "message": "PlexiJoin encryption service is not available",
                }
            },
        )

    return PlexiJoinManager(
        db=db,
        admin_logger=admin_logger,
        encryption_service=encryption_service,
    )


# === Outbound Connections ===


@router.get("/plexijoin/connections")
async def list_connections(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by connection status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """List all outbound federation connections."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        manager = _get_plexijoin_manager(request)
        result = manager.list_connections(status=status, page=page, per_page=per_page)

        connections_data = []
        for conn in result["connections"]:
            connections_data.append(
                {
                    "id": conn["id"],
                    "remote_instance_id": conn["remote_instance_id"],
                    "remote_url": conn["remote_url"],
                    "status": conn["status"],
                    "connected_at": conn.get("connected_at"),
                    "messages_in": conn.get("messages_in", 0),
                    "messages_out": conn.get("messages_out", 0),
                    "last_activity": conn.get("last_activity"),
                    "note": conn.get("note"),
                    "created_at": conn["created_at"],
                    "created_by": conn["created_by"],
                }
            )

        return {
            "connections": connections_data,
            "total": result["total"],
            "page": result["page"],
            "per_page": result["per_page"],
            "pages": result["pages"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PlexiJoin list connections error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.get("/plexijoin/connections/{connection_id}")
async def get_connection(request: Request, connection_id: int):
    """Get a specific connection by ID."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        manager = _get_plexijoin_manager(request)
        connection = manager.get_connection(connection_id)

        if not connection:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Connection not found"}},
            )

        return {
            "id": connection["id"],
            "remote_instance_id": connection["remote_instance_id"],
            "remote_url": connection["remote_url"],
            "status": connection["status"],
            "connected_at": connection.get("connected_at"),
            "messages_in": connection.get("messages_in", 0),
            "messages_out": connection.get("messages_out", 0),
            "last_activity": connection.get("last_activity"),
            "note": connection.get("note"),
            "created_at": connection["created_at"],
            "created_by": connection["created_by"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PlexiJoin get connection error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/plexijoin/connections")
async def create_connection(request: Request):
    """Create a new outbound federation connection."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        body = await request.json()

        remote_instance_id = body.get("remote_instance_id")
        remote_url = body.get("remote_url")
        shared_key = body.get("shared_key")
        note = body.get("note")

        if not remote_instance_id or not remote_url or not shared_key:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "remote_instance_id, remote_url, and shared_key are required",
                    }
                },
            )

        manager = _get_plexijoin_manager(request)

        connection = manager.create_connection(
            remote_instance_id=remote_instance_id,
            remote_url=remote_url,
            shared_key=shared_key,
            note=note,
            admin_id=_admin,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        return {"success": True, "connection_id": connection["id"]}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"PlexiJoin create connection error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.delete("/plexijoin/connections/{connection_id}")
async def delete_connection(request: Request, connection_id: int):
    """Disconnect and delete an outbound connection."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        manager = _get_plexijoin_manager(request)

        success = manager.delete_connection(
            connection_id=connection_id,
            admin_id=_admin,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        if not success:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Connection not found"}},
            )

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PlexiJoin delete connection error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.post("/plexijoin/connections/{connection_id}/test")
async def test_connection(request: Request, connection_id: int):
    """Test connectivity to a remote instance."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        manager = _get_plexijoin_manager(request)
        result = await manager.test_connection(connection_id)

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PlexiJoin test connection error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


# === Inbound Requests ===


@router.get("/plexijoin/requests")
async def list_requests(
    request: Request,
    status: Optional[str] = Query(None, description="Filter by request status"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    """List inbound federation requests."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        manager = _get_plexijoin_manager(request)
        result = manager.list_requests(status=status, page=page, per_page=per_page)

        requests_data = []
        for req in result["requests"]:
            requests_data.append(
                {
                    "id": req["id"],
                    "remote_instance_id": req["remote_instance_id"],
                    "remote_url": req["remote_url"],
                    "requested_by": req.get("requested_by"),
                    "note": req.get("note"),
                    "status": req["status"],
                    "requested_at": req["requested_at"],
                    "reviewed_at": req.get("reviewed_at"),
                    "reviewed_by": req.get("reviewed_by"),
                }
            )

        return {
            "requests": requests_data,
            "total": result["total"],
            "page": result["page"],
            "per_page": result["per_page"],
            "pages": result["pages"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PlexiJoin list requests error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.post("/plexijoin/requests/{request_id}/approve")
async def approve_request(request: Request, request_id: int):
    """Approve an inbound federation request."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        manager = _get_plexijoin_manager(request)

        result = manager.approve_request(
            request_id=request_id,
            admin_id=_admin,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        return {"success": True, "request": result}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"PlexiJoin approve request error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.post("/plexijoin/requests/{request_id}/deny")
async def deny_request(request: Request, request_id: int):
    """Deny an inbound federation request."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        manager = _get_plexijoin_manager(request)

        result = manager.deny_request(
            request_id=request_id,
            admin_id=_admin,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

        return {"success": True, "request": result}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(f"PlexiJoin deny request error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


# === Status & Analytics ===


@router.get("/plexijoin/status")
async def get_status(request: Request):
    """Get federation health summary."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        manager = _get_plexijoin_manager(request)
        status = manager.get_status_summary()

        return status
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PlexiJoin status error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.get("/plexijoin/traffic")
async def get_traffic(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
):
    """Get traffic data for the specified hours."""
    check_host_restriction(request)
    _admin = get_admin_from_token(request)

    try:
        manager = _get_plexijoin_manager(request)
        traffic = manager.get_traffic_data(hours=hours)

        return {"hours": hours, "data": traffic}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PlexiJoin traffic error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )
