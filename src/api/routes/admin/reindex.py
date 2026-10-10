from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from .utils import (
    check_host_restriction,
    require_admin_permission,
    require_approved_request,
)
from src.api.schemas.common import ErrorResponse, SnowflakeIDParam
import src.core.search as search_module
import utils.logger as logger
import threading
import time

router = APIRouter()

# Track reindex status for monitoring. The in-progress flag is guarded by a
# lock so the check-and-set is atomic across concurrent requests.
_reindex_status = {
    "in_progress": False,
    "last_run": None,
    "last_count": 0,
    "last_duration_ms": 0,
    "last_incremental": True,
    "last_error": None,
    # Live progress for the currently-running (or last completed) run.
    "indexed_so_far": 0,
    "cancel_requested": False,
}
_reindex_lock = threading.Lock()
_cancel_event = threading.Event()


class ReindexRequest(BaseModel):
    force: bool = False


def _require_search_ready() -> None:
    if not search_module._setup_complete or search_module._manager is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Search module not initialized"}},
        )


@router.post(
    "/search/reindex",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def reindex_search(request: Request, body: ReindexRequest | None = None):
    """
    Run a full/incremental reindex inline.

    The manager streams messages in bounded pages: only one page of decrypted
    content exists in memory at any moment, so this never materializes the
    whole table's plaintext (the old bulk-decrypt design).
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.data.reindex")
    # Mass-decrypts messages page-by-page into the search index: require an
    # approved approval request just like the standalone path.
    require_approved_request("search.reindex")
    _require_search_ready()

    force = bool(body.force) if body else False
    try:
        started = time.perf_counter()
        with _reindex_lock:
            _reindex_status["indexed_so_far"] = 0
            _reindex_status["cancel_requested"] = False
        _cancel_event.clear()
        indexed = await run_in_threadpool(
            search_module._manager.reindex_all,
            force,
            None,
            _should_cancel,
            _on_progress,
        )
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Reindex failed"}},
        )


@router.post(
    "/search/reindex/standalone",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        409: {"model": ErrorResponse, "description": "Reindex already in progress"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def reindex_standalone(request: Request, body: ReindexRequest | None = None):
    """
    Start a background reindex that streams raw messages from the database
    in bounded pages and decrypts each page with the server's fully
    initialized encryption context.

    Plaintext never accumulates: each page is decrypted, indexed, and
    released before the next page is selected, so memory holds at most one
    page (default 200 messages) of decrypted content regardless of table
    size.

    Pass {"force": true} in the body to do a full rebuild; otherwise the
    reindex is incremental (skips messages whose source has not changed
    since the last index). POST /search/reindex/cancel stops a running job.
    """
    global _reindex_status

    check_host_restriction(request)
    # Mass-decrypts messages into the search index (in bounded pages):
    # elevated permission plus an approved approval request are required.
    require_admin_permission(request, "admin.data.reindex")
    require_approved_request("search.reindex")
    _require_search_ready()

    # Atomically claim the in-progress flag so two simultaneous requests can
    # never both start background reindex threads.
    with _reindex_lock:
        if _reindex_status["in_progress"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": {"code": 409, "message": "Reindex already in progress"}
                },
            )
        _reindex_status["in_progress"] = True
        _reindex_status["last_error"] = None
        _reindex_status["last_incremental"] = not (bool(body.force) if body else False)
        _reindex_status["indexed_so_far"] = 0
        _reindex_status["cancel_requested"] = False

    force = bool(body.force) if body else False
    _cancel_event.clear()

    def _do_reindex():
        global _reindex_status
        started = time.perf_counter()
        try:
            indexed = search_module._manager.reindex_all(
                force=force,
                should_cancel=_should_cancel,
                on_progress=_on_progress,
            )

            duration_ms = int((time.perf_counter() - started) * 1000)
            with _reindex_lock:
                _reindex_status["last_run"] = time.time()
                _reindex_status["last_count"] = indexed
                _reindex_status["last_duration_ms"] = duration_ms
            logger.info(
                f"Standalone reindex complete: {indexed} messages indexed in {duration_ms}ms "
                f"(incremental={not force}, paged streaming)"
            )
        except Exception as e:
            with _reindex_lock:
                _reindex_status["last_error"] = str(e)
            logger.error(f"Standalone reindex failed: {e}", exc_info=True)
        finally:
            with _reindex_lock:
                _reindex_status["in_progress"] = False
                _reindex_status["cancel_requested"] = False

    thread = threading.Thread(target=_do_reindex, name="search-reindex", daemon=True)
    thread.start()

    return {
        "success": True,
        "message": "Reindex started in background (paged streaming)",
        "incremental": not force,
        "status_url": "/api/v1/admin/search/reindex/status",
        "cancel_url": "/api/v1/admin/search/reindex/cancel",
    }


def _should_cancel() -> bool:
    return _cancel_event.is_set()


def _on_progress(indexed_so_far: int, pages_done: int) -> None:
    with _reindex_lock:
        _reindex_status["indexed_so_far"] = indexed_so_far


@router.post(
    "/search/reindex/cancel",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
    },
)
async def reindex_cancel(request: Request):
    """Ask a running background reindex to stop at the next page boundary."""
    check_host_restriction(request)
    require_admin_permission(request, "admin.data.reindex")

    _cancel_event.set()
    with _reindex_lock:
        _reindex_status["cancel_requested"] = True
    return {"success": True, "message": "Cancellation requested"}


@router.get(
    "/search/reindex/status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
    },
)
async def reindex_status(request: Request):
    """Get current reindex status, including live progress."""
    check_host_restriction(request)
    require_admin_permission(request, "admin.data.reindex")

    with _reindex_lock:
        status = dict(_reindex_status)
    return {
        "in_progress": status["in_progress"],
        "last_run": status["last_run"],
        "last_count": status["last_count"],
        "last_duration_ms": status.get("last_duration_ms", 0),
        "last_incremental": status.get("last_incremental", True),
        "last_error": status["last_error"],
        "indexed_so_far": status.get("indexed_so_far", 0),
        "cancel_requested": status.get("cancel_requested", False),
    }


@router.post(
    "/search/reindex/conversation/{conversation_id}",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def reindex_conversation(
    request: Request,
    conversation_id: SnowflakeIDParam,
    body: ReindexRequest | None = None,
):
    check_host_restriction(request)
    require_admin_permission(request, "admin.data.reindex")
    _require_search_ready()

    force = bool(body.force) if body else False
    try:
        indexed = search_module._manager.reindex_conversation(conversation_id, force)
        return {
            "success": True,
            "indexed": indexed,
            # Snowflake as a decimal string: a JS Number cannot hold a 64-bit
            # conversation id exactly, so echoing it numerically is unsafe.
            "conversation_id": str(conversation_id),
            "incremental": not force,
        }
    except Exception as e:
        logger.error(f"Reindex conversation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Reindex failed"}},
        )
