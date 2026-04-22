from fastapi import APIRouter, Request, HTTPException
from .utils import check_host_restriction, get_admin_from_token
import src.core.search as search_module
import utils.logger as logger

router = APIRouter()


@router.post("/search/reindex")
async def reindex_search(request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)

    if not search_module._setup_complete or search_module._manager is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Search module not initialized"}},
        )

    try:
        indexed = search_module._manager.reindex_all()
        return {"success": True, "indexed": indexed}
    except Exception as e:
        logger.error(f"Reindex failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/search/reindex/conversation/{conversation_id}")
async def reindex_conversation(request: Request, conversation_id: int):
    check_host_restriction(request)
    get_admin_from_token(request)

    if not search_module._setup_complete or search_module._manager is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Search module not initialized"}},
        )

    try:
        indexed = search_module._manager.reindex_conversation(conversation_id)
        return {"success": True, "indexed": indexed, "conversation_id": conversation_id}
    except Exception as e:
        logger.error(f"Reindex conversation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
