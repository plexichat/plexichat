from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
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
    "last_duration_ms": 0,
    "last_incremental": True,
    "last_error": None,
}


class ReindexRequest(BaseModel):
    force: bool = False


@router.post("/search/reindex")
async def reindex_search(request: Request, body: ReindexRequest | None = None):
    check_host_restriction(request)
    get_admin_from_token(request)

    if not search_module._setup_complete or search_module._manager is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Search module not initialized"}},
        )

    force = bool(body.force) if body else False
    try:
        started = time.perf_counter()
        indexed = await run_in_threadpool(search_module._manager.reindex_all, force)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "success": True,
            "indexed": indexed,
            "duration_ms": duration_ms,
            "incremental": not force,
        }
    except Exception as e:
        logger.error(f"Reindex failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/search/reindex/standalone")
async def reindex_standalone(request: Request, body: ReindexRequest | None = None):
    """
    Reindex search by reading raw encrypted messages from the database
    and decrypting them using the server's fully initialized encryption context.

    This bypasses the messaging module's access layer and uses the encryption
    module directly, ensuring we have the correct encryption context.

    Pass {\"force\": true} in the body to do a full rebuild; otherwise the
    reindex is incremental (skips messages whose source has not changed
    since the last index).
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

    force = bool(body.force) if body else False

    def _do_reindex():
        global _reindex_status
        _reindex_status["in_progress"] = True
        _reindex_status["last_error"] = None
        _reindex_status["last_incremental"] = not force
        started = time.perf_counter()

        try:
            from src.utils.encryption import (
                decrypt_message,
                is_message_encrypted,
                decrypt_data,
            )

            mgr = search_module._manager
            db = mgr._db

            if force:
                msg_query = (
                    "SELECT id, content, content_encrypted, author_id, "
                    "conversation_id, created_at, updated_at "
                    "FROM msg_messages WHERE deleted = 0"
                )
                msg_params: tuple = ()
            else:
                msg_query = (
                    "SELECT m.id, m.content, m.content_encrypted, m.author_id, "
                    "m.conversation_id, m.created_at, m.updated_at "
                    "FROM msg_messages m "
                    "LEFT JOIN search_message_index i ON m.id = i.message_id "
                    "WHERE m.deleted = 0 "
                    "AND (i.message_id IS NULL "
                    "OR m.updated_at > COALESCE(i.source_updated_at, 0))"
                )
                msg_params = ()

            rows = (
                db.fetch_all(msg_query, msg_params)
                if msg_params
                else db.fetch_all(msg_query)
            )

            indexed = 0
            for msg in rows:
                content = msg["content"] or ""
                content_encrypted = msg.get("content_encrypted")
                message_id = msg["id"]

                plaintext = ""

                if is_message_encrypted(content):
                    try:
                        plaintext = decrypt_message(content, message_id)
                    except Exception:
                        plaintext = ""
                elif content == "[encrypted]" and content_encrypted:
                    try:
                        plaintext = decrypt_data(content_encrypted)
                    except Exception:
                        plaintext = ""
                else:
                    plaintext = content

                mgr.index_message(
                    message_id=message_id,
                    content=plaintext,
                    metadata={
                        "author_id": msg["author_id"],
                        "conversation_id": msg["conversation_id"],
                        "created_at": msg["created_at"],
                        "source_updated_at": msg.get("updated_at", 0) or 0,
                    },
                )
                indexed += 1

            if not force:
                stale = db.fetch_all(
                    "SELECT i.message_id FROM search_message_index i "
                    "LEFT JOIN msg_messages m ON i.message_id = m.id "
                    "WHERE m.id IS NULL OR m.deleted = 1"
                )
                for row in stale:
                    mgr.remove_from_index(row["message_id"])

            duration_ms = int((time.perf_counter() - started) * 1000)
            _reindex_status["last_run"] = time.time()
            _reindex_status["last_count"] = indexed
            _reindex_status["last_duration_ms"] = duration_ms
            logger.info(
                f"Standalone reindex complete: {indexed} messages indexed in {duration_ms}ms "
                f"(incremental={not force})"
            )
        except Exception as e:
            _reindex_status["last_error"] = str(e)
            logger.error(f"Standalone reindex failed: {e}", exc_info=True)
        finally:
            _reindex_status["in_progress"] = False

    thread = threading.Thread(target=_do_reindex)
    thread.start()

    return {
        "success": True,
        "message": "Reindex started in background",
        "incremental": not force,
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
        "last_duration_ms": _reindex_status.get("last_duration_ms", 0),
        "last_incremental": _reindex_status.get("last_incremental", True),
        "last_error": _reindex_status["last_error"],
    }


@router.post("/search/reindex/conversation/{conversation_id}")
async def reindex_conversation(
    request: Request, conversation_id: int, body: ReindexRequest | None = None
):
    check_host_restriction(request)
    get_admin_from_token(request)

    if not search_module._setup_complete or search_module._manager is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Search module not initialized"}},
        )

    force = bool(body.force) if body else False
    try:
        indexed = search_module._manager.reindex_conversation(conversation_id, force)
        return {
            "success": True,
            "indexed": indexed,
            "conversation_id": conversation_id,
            "incremental": not force,
        }
    except Exception as e:
        logger.error(f"Reindex conversation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
