from fastapi import APIRouter, Request, HTTPException
from starlette.concurrency import run_in_threadpool
from .utils import check_host_restriction, get_admin_from_token
import src.core.search as search_module
import utils.logger as logger
import threading
import time

router = APIRouter()

# Track reindex status for monitoring
_reindex_status = {
    "in_progress": False,
    "last_run": None,
    "last_count": 0,
    "last_error": None,
}


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
        indexed = await run_in_threadpool(search_module._manager.reindex_all)
        return {"success": True, "indexed": indexed}
    except Exception as e:
        logger.error(f"Reindex failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/search/reindex/standalone")
async def reindex_standalone(request: Request):
    """
    Reindex search by reading raw encrypted messages from the database
    and decrypting them using the server's fully initialized encryption context.

    This bypasses the messaging module's access layer and uses the encryption
    module directly, ensuring we have the correct encryption context.
    """
    global _reindex_status

    check_host_restriction(request)
    get_admin_from_token(request)

    if not search_module._setup_complete or search_module._manager is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Search module not initialized"}},
        )

    if _reindex_status["in_progress"]:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": 409, "message": "Reindex already in progress"}},
        )

    def _do_reindex():
        global _reindex_status
        _reindex_status["in_progress"] = True
        _reindex_status["last_error"] = None

        try:
            from src.utils.encryption import (
                decrypt_message,
                is_message_encrypted,
                decrypt_data,
            )

            mgr = search_module._manager
            db = mgr._db

            # Get all non-deleted messages (raw encrypted content)
            messages = db.fetch_all(
                "SELECT id, content, content_encrypted FROM msg_messages WHERE deleted = 0"
            )

            indexed = 0
            for msg in messages:
                content = msg["content"] or ""
                content_encrypted = msg.get("content_encrypted")
                message_id = msg["id"]

                plaintext = ""

                # New encrypted format: starts with 'ENC:'
                if is_message_encrypted(content):
                    try:
                        plaintext = decrypt_message(content, message_id)
                    except Exception:
                        plaintext = ""
                # Legacy format
                elif content == "[encrypted]" and content_encrypted:
                    try:
                        plaintext = decrypt_data(content_encrypted)
                    except Exception:
                        plaintext = ""
                else:
                    plaintext = content

                # Get metadata for indexing
                meta_row = db.fetch_one(
                    "SELECT author_id, conversation_id, created_at FROM msg_messages WHERE id = ?",
                    (message_id,),
                )

                if meta_row:
                    mgr.index_message(
                        message_id=message_id,
                        content=plaintext,
                        metadata={
                            "author_id": meta_row["author_id"],
                            "conversation_id": meta_row["conversation_id"],
                            "created_at": meta_row["created_at"],
                        },
                    )

                indexed += 1

            _reindex_status["last_run"] = time.time()
            _reindex_status["last_count"] = indexed
            logger.info(f"Standalone reindex complete: {indexed} messages indexed")
        except Exception as e:
            _reindex_status["last_error"] = str(e)
            logger.error(f"Standalone reindex failed: {e}", exc_info=True)
        finally:
            _reindex_status["in_progress"] = False

    # Run in background thread so API returns immediately
    thread = threading.Thread(target=_do_reindex)
    thread.start()

    return {
        "success": True,
        "message": "Reindex started in background",
        "status_url": "/api/v1/admin/search/reindex/status",
    }


@router.get("/search/reindex/status")
async def reindex_status(request: Request):
    """Get current reindex status."""
    check_host_restriction(request)
    get_admin_from_token(request)

    return {
        "in_progress": _reindex_status["in_progress"],
        "last_run": _reindex_status["last_run"],
        "last_count": _reindex_status["last_count"],
        "last_error": _reindex_status["last_error"],
    }


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
