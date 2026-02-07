"""
Admin database monitoring routes.
"""

from fastapi import APIRouter, Request, HTTPException
from .utils import check_host_restriction, get_admin_from_token
import src.api as api
import utils.logger as logger

router = APIRouter()

@router.get("/database/pool-health")
async def get_db_pool_health(request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    db = api.get_db()
    if not db:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Database not available"}})
    try:
        stats = db.get_pool_stats()
        return stats
    except Exception as e:
        logger.error(f"DB pool health error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})
