from fastapi import APIRouter, Depends, Query
from typing import Optional

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import raise_bad_request, raise_forbidden, raise_internal

router = APIRouter()


@router.get(
    "/users/@me/audit-logs",
    summary="Get your visible audit logs",
    responses={401: {"model": ErrorResponse}},
)
async def get_user_audit_logs(
    server_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = Query(25, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
):
    servers_mod = api.get_servers()
    if not servers_mod:
        raise_internal("Servers module not available")

    db = api.get_db()
    if not db:
        raise_internal("Database not available")

    try:
        servers = servers_mod.get_servers(current_user.user_id)
    except Exception:
        servers = []

    visible_server_ids = []
    for srv in servers or []:
        sid = getattr(srv, "id", None) or (
            srv.get("id") if isinstance(srv, dict) else None
        )
        if sid:
            try:
                perms = servers_mod.get_permissions(current_user.user_id, int(sid))
                if perms.get("server.view_audit_log", False):
                    visible_server_ids.append(int(sid))
            except Exception:
                continue

    if not visible_server_ids:
        return {"entries": []}

    target_sid = None
    if server_id:
        try:
            target_sid = int(server_id)
        except ValueError:
            raise_bad_request("Invalid server ID")
        if target_sid not in visible_server_ids:
            raise_forbidden("No audit log access for this server")
        visible_server_ids = [target_sid]

    placeholders = ",".join("?" for _ in visible_server_ids)
    query = f"SELECT * FROM srv_audit_log WHERE server_id IN ({placeholders})"
    params: list = list(visible_server_ids)

    if action:
        query += " AND action = ?"
        params.append(action)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    try:
        rows = db.fetch_all(query, tuple(params))
        entries = []
        for row in rows:
            data = dict(row)
            entries.append(
                {
                    "id": data.get("id"),
                    "server_id": data.get("server_id"),
                    "user_id": data.get("user_id"),
                    "action": data.get("action"),
                    "target_type": data.get("target_type"),
                    "target_id": data.get("target_id"),
                    "changes": data.get("changes"),
                    "reason": data.get("reason"),
                    "created_at": data.get("created_at"),
                }
            )
        return {"entries": entries}
    except Exception as e:
        logger.error(f"Failed to get user audit logs: {e}")
        raise_internal("Internal server error")
