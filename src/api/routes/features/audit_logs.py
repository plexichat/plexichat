from fastapi import APIRouter, Depends, Query
from typing import Optional

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.api.routes.users.helpers import require_user_token
from .common import parse_id, raise_forbidden, raise_internal

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
    require_user_token(current_user)
    servers_mod = api.get_servers()
    if not servers_mod:
        raise_internal("Servers module not available")

    db = api.get_db()
    if not db:
        raise_internal("Database not available")

    # SECURITY: validate the optional server filter at the boundary with the
    # shared snowflake gate, before any server scan. A bare ``int()`` accepted
    # Unicode digit homoglyphs (``"١٢٣"``), PEP 515 separators (``"1_2"``),
    # explicit signs/whitespace and negatives -- an id one hop reads as invalid
    # text must not become a live server filter here (parser differential).
    # Parsing up front also keeps the 400 contract consistent whether or not
    # the caller happens to have any visible audit-log servers.
    target_sid = parse_id(server_id, "server ID") if server_id else None

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
            except Exception as perm_error:
                logger.debug(
                    f"audit log permission check failed for {sid}: {perm_error}"
                )
                continue

    if not visible_server_ids:
        return {"entries": []}

    if target_sid is not None:
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
            if isinstance(row, dict):
                data = row
            else:
                data = {
                    "id": row[0],
                    "server_id": row[1],
                    "user_id": row[2],
                    "action": row[3],
                    "target_type": row[4],
                    "target_id": row[5],
                    "changes": row[6],
                    "reason": row[7],
                    "created_at": row[8],
                }
            entries.append(
                {
                    # Snowflake ids are emitted as strings: a JS Number cannot
                    # hold a 64-bit id exactly, so a numeric id would be rounded
                    # by the client and subsequent lookups would miss.
                    "id": str(data.get("id")) if data.get("id") is not None else None,
                    "server_id": str(data.get("server_id"))
                    if data.get("server_id") is not None
                    else None,
                    "user_id": str(data.get("user_id"))
                    if data.get("user_id") is not None
                    else None,
                    "action": data.get("action"),
                    "target_type": data.get("target_type"),
                    "target_id": str(data.get("target_id"))
                    if data.get("target_id") is not None
                    else None,
                    "changes": data.get("changes"),
                    "reason": data.get("reason"),
                    "created_at": data.get("created_at"),
                }
            )
        return {"entries": entries}
    except Exception:
        logger.error("Failed to get user audit logs", exc_info=True)
        raise_internal("Internal server error")
