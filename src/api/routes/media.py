"""
Media API routes - File uploads, deduplication, and content moderation.

Provides endpoints for:
- Hash-based content reporting
- Chunked/resumable uploads
- Compression status
"""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
    Request,
    UploadFile,
    File,
    Query,
)
from typing import Any, Dict, Optional
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

import utils.logger as logger
import src.api as api
from src.api.lru_cache import LRUCache
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import require_user_token
from src.core.config.snapshot import VersionedConfigSnapshot, file_version
from src.api.schemas.media import (
    HashReportRequest,
    HashReportResponse,
    ChunkedUploadSessionRequest,
    ChunkedUploadSessionResponse,
    ChunkUploadResponse,
    CompressionStatusResponse,
    HashStatusResponse,
    CompleteUploadResponse,
    UploadSessionsResponse,
    UploadSessionInfo,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.media.exceptions import (
    MediaError,
    ProxyError,
    ProxyFetchError,
)

router = APIRouter(prefix="/media", tags=["Media"])


# === Hash-report abuse controls ===
#
# ``media.report_hash`` auto-blocks a hash once ``auto_block_threshold``
# *pending* reports exist for it (default 5, see the deduplication manager).
# The count used to include every row from every reporter, so a single
# authenticated account could report the same hash five times and silently
# block that hash for the whole instance — a content-DoS primitive — and could
# otherwise flood the moderation queue without bound. Mirror the per-user
# throttle used by the other report routes and dedup at the trust boundary so
# the threshold reflects *distinct* reporters.
MAX_HASH_REPORTS_PER_MINUTE = 10
HASH_REPORT_WINDOW_SECONDS = 60


def _check_hash_report_rate_limit(user_id: int) -> None:
    """Charge a per-user bucket for hash-report submissions (fail closed)."""
    try:
        from src.core.database import check_rate_limit

        allowed, _remaining = check_rate_limit(
            f"media:hash_report:{user_id}",
            MAX_HASH_REPORTS_PER_MINUTE,
            HASH_REPORT_WINDOW_SECONDS,
        )
    except Exception as e:
        logger.error(f"Hash-report rate limit unavailable: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": 503,
                    "message": "Hash reporting temporarily unavailable",
                }
            },
        )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": 429,
                    "message": "Too many hash reports submitted. Please slow down.",
                }
            },
        )


def _existing_pending_hash_report(user_id: int, hash_value: str):
    """Return this reporter's pending report id for ``hash_value``, if any.

    A reporter may not push a single hash past the auto-block threshold on
    their own; the moderation signal must come from distinct accounts. The
    database is the source of truth, and an unavailable database fails closed
    so dedup cannot be skipped by taking the storage tier down.
    """
    db = api.get_db()
    if db is None:
        logger.error("Hash-report dedup unavailable: database not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": 503,
                    "message": "Hash reporting temporarily unavailable",
                }
            },
        )
    row = db.fetch_one(
        "SELECT id FROM media_hash_reports "
        "WHERE reporter_id = ? AND hash_value = ? AND status = 'pending' "
        "ORDER BY reported_at ASC LIMIT 1",
        (user_id, hash_value),
    )
    if not row:
        return None
    return row["id"] if isinstance(row, dict) else row[0]


# === Proxy request policy (parsed once per config version) ===


# The proxy policy is read on every uncached proxy fetch, so the version token
# must stay cheap.  Its in-memory parts (loader identity + ``repr`` of the tiny
# ``proxy_rate_limit`` mapping) already catch a new ``config.setup()`` and any
# in-place edit made by tests/admin reloads on the very next access.  Only the
# ``os.stat`` of the config file is genuinely expensive; memoise just that
# piece behind a short staleness ceiling.  Unlike a blanket snapshot ceiling,
# this keeps immediate detection of in-memory changes while cutting the stat
# traffic on the hot path to at most once per window.
_PROXY_FILE_VERSION_MAX_AGE_SECONDS = 5.0
_proxy_file_version_cache: dict[str, Any] = {"at": 0.0, "version": None}


def _proxy_config_version() -> tuple:
    """Version token for the cached proxy policy, with a throttled file stat.

    A new loader instance (``config.setup`` in tests/reload paths) or a direct
    in-place change to the small ``proxy_rate_limit`` mapping is reflected by
    the cheap in-memory parts of the token on the very next access.  The
    ``file_version`` component (an ``os.stat``) is memoised for a short window
    so an on-disk edit is still observed, but at most once per ceiling rather
    than on every proxy request.
    """
    import time as _time

    import utils.config as _config

    loader = getattr(_config, "_config_instance", None)
    path = getattr(loader, "config_path", None)
    try:
        rate_cfg = _config.get("media", {}).get("proxy_rate_limit", {})
    except Exception:
        rate_cfg = {}

    cached = _proxy_file_version_cache
    now = _time.monotonic()
    if cached["version"] is None or (
        now - cached["at"]
    ) >= _PROXY_FILE_VERSION_MAX_AGE_SECONDS:
        cached["version"] = file_version(path)
        cached["at"] = now
    return (id(loader), cached["version"], repr(rate_cfg))


def _load_proxy_policy() -> Dict[str, Any]:
    """Read and validate the proxy rate-limit settings once per version."""
    import utils.config as _config

    rate_cfg = _config.get("media", {}).get("proxy_rate_limit", {})
    return {
        "requests_per_window": int(rate_cfg.get("requests_per_window", 120)),
        "window_seconds": int(rate_cfg.get("window_seconds", 60)),
    }


# The snapshot checks its version on every access so the cheap in-memory parts
# of the token (loader id + ``repr(rate_cfg)``) are seen immediately.  The
# expensive ``os.stat`` inside that token is throttled separately (see
# ``_proxy_config_version``).
_proxy_policy_snapshot = VersionedConfigSnapshot(
    _load_proxy_policy, version_fn=_proxy_config_version
)


def _proxy_rate_limit_params() -> tuple[int, int]:
    """Return ``(requests_per_window, window_seconds)`` from the snapshot."""
    policy = _proxy_policy_snapshot.snapshot()
    return policy["requests_per_window"], policy["window_seconds"]


# The route-level scheme check is a pure function of the URL string. Cache its
# verdict so repeated renders of the same remote image do not re-run urlparse
# on the event loop. The core proxy still performs its full SSRF validation on
# every fetch; this cache only short-circuits the cheap early-reject check.
_proxy_url_verdict_cache = LRUCache(maxsize=2048)


def _is_allowed_proxy_url(url: str) -> bool:
    """Whether ``url`` uses an http(s) scheme (memoised by exact URL)."""
    cached = _proxy_url_verdict_cache.get(url)
    if cached is not None:
        return cached
    from urllib.parse import urlparse

    verdict = urlparse(url).scheme in ("http", "https")
    _proxy_url_verdict_cache[url] = verdict
    return verdict


# === Content Reporting ===


@router.post(
    "/report",
    response_model=HashReportResponse,
    summary="Report content hash",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def report_content_hash(
    report: HashReportRequest,
    request: Request,
    user: TokenInfo = Depends(get_current_user),
) -> HashReportResponse:
    """
    Report a file hash for content moderation.

    This allows users to report potentially harmful content by its hash.
    Multiple reports on the same hash may trigger automatic blocking.
    Supports both SHA-256 and perceptual hashes for image similarity detection.
    """
    # A bot token's ``user_id`` is its human owner. Reject bot credentials
    # before charging the owner's ``media:hash_report:<id>`` bucket.
    require_user_token(user)
    from src.core import media

    # Per-user throttle and reporter-level dedup run BEFORE any moderation
    # write. Each report would otherwise move the hash closer to the global
    # auto-block threshold, so an unthrottled caller could block arbitrary
    # content simply by reporting the same hash repeatedly.
    _check_hash_report_rate_limit(user.user_id)

    try:
        existing = _existing_pending_hash_report(user.user_id, report.hash_value)
        if existing is not None:
            return HashReportResponse(
                success=True,
                report_id=str(existing),
                message="Report already submitted",
            )

        report_id = media.report_hash(
            hash_value=report.hash_value,
            reporter_id=user.user_id,
            reason=report.reason,
            details=report.details,
            phash_value=report.phash_value,
            uploader_id=int(report.uploader_id) if report.uploader_id else None,
            message_id=int(report.message_id) if report.message_id else None,
            attachment_url=report.attachment_url,
            block_uploader=report.block_uploader,
        )

        logger.info(
            f"User {user.user_id} reported hash {report.hash_value[:16]}... (block_uploader={report.block_uploader})"
        )

        return HashReportResponse(
            success=True,
            report_id=str(report_id),
            message="Report submitted successfully",
        )
    except HTTPException:
        # A fail-closed 503 from the dedup gate must not be rewrapped as a 500.
        raise
    except Exception as e:
        logger.error(
            f"Failed to submit hash report for user {user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/hash/{hash_value}/status",
    response_model=HashStatusResponse,
    summary="Check hash status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def check_hash_status(
    hash_value: str, user: TokenInfo = Depends(get_current_user)
) -> HashStatusResponse:
    """Check if a hash is blocked."""
    from src.core import media

    try:
        is_blocked, reason = media.is_hash_blocked(hash_value)
        logger.debug(
            f"User {user.user_id} checked status for hash {hash_value[:16]}... (is_blocked={is_blocked})"
        )

        return HashStatusResponse(
            hash_value=hash_value,
            is_blocked=is_blocked,
            reason=reason if is_blocked else None,
        )
    except Exception as e:
        logger.error(
            f"Failed to check hash status for user {user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== Chunked Uploads ====================


@router.post(
    "/upload/session",
    response_model=ChunkedUploadSessionResponse,
    summary="Create upload session",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid request or file too large",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_upload_session(
    session_request: ChunkedUploadSessionRequest,
    user: TokenInfo = Depends(get_current_user),
) -> ChunkedUploadSessionResponse:
    """
    Create a chunked upload session for large files.

    Returns session details including chunk size and total chunks.
    Use the session_id to upload chunks via POST /media/upload/chunk.
    """
    from src.core import media

    try:
        session = media.create_upload_session(
            user_id=user.user_id,
            filename=session_request.filename,
            content_type=session_request.content_type,
            total_size=session_request.total_size,
        )

        if not session:
            logger.warning(
                f"Failed to create upload session for user {user.user_id} (file too large or invalid)"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Failed to create upload session. File may be too large.",
                    }
                },
            )

        logger.info(
            f"User {user.user_id} created upload session {session.id} for '{session_request.filename}'"
        )

        return ChunkedUploadSessionResponse(
            session_id=session.id,
            chunk_size=session.chunk_size,
            total_chunks=session.total_chunks,
            expires_at=session.expires_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to create upload session for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/upload/chunk/{session_id}",
    response_model=ChunkUploadResponse,
    summary="Upload chunk",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid chunk or session"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_chunk(
    session_id: str,
    chunk_index: int,
    chunk_checksum: Optional[str] = None,
    file: UploadFile = File(...),
    user: TokenInfo = Depends(get_current_user),
) -> ChunkUploadResponse:
    """
    Upload a chunk to an existing upload session.

    Args:
        session_id: The upload session ID
        chunk_index: Zero-based index of this chunk
        chunk_checksum: Optional SHA-256 checksum (lowercase hex) for verification
        file: The chunk data
    """
    from src.core import media

    try:

        def _upload_chunk_stream():
            stream = file.file
            stream.seek(0)
            return media.upload_chunk_stream(
                session_id=session_id,
                user_id=user.user_id,
                chunk_index=chunk_index,
                chunk_stream=stream,
                chunk_checksum=chunk_checksum,
            )

        result = await run_in_threadpool(_upload_chunk_stream)

        if not result.success:
            logger.warning(
                f"Chunk upload failed for session {session_id}, user {user.user_id}, index {chunk_index}: {result.error}"
            )
        else:
            logger.debug(
                f"User {user.user_id} uploaded chunk {chunk_index} for session {session_id} ({result.progress_percent}%)"
            )

        return ChunkUploadResponse(
            success=result.success,
            chunk_index=result.chunk_index,
            uploaded_chunks=result.uploaded_chunks,
            total_chunks=result.total_chunks,
            progress_percent=result.progress_percent,
            is_complete=result.is_complete,
            error=result.error,
        )
    except ValueError as e:
        logger.warning(
            f"Invalid chunk upload request for session {session_id}, user {user.user_id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(
            f"Chunk upload failed for session {session_id}, user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/upload/complete/{session_id}",
    response_model=CompleteUploadResponse,
    summary="Complete upload session",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Upload session not complete or not found",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def complete_upload_session(
    session_id: str, user: TokenInfo = Depends(get_current_user)
) -> CompleteUploadResponse:
    """
    Complete a chunked upload session and process the file.

    Returns the final upload result with file URL.
    """
    from src.core import media

    try:
        session = media.complete_upload_session_stream(session_id, user.user_id)

        if session is None:
            logger.warning(
                f"Failed to complete upload session {session_id} for user {user.user_id} (not complete or not found)"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Upload session not complete or not found",
                    }
                },
            )

        try:

            def _upload_complete_file():
                assert session.temp_path is not None
                with open(session.temp_path, "rb") as f:
                    return media.upload_stream(
                        user_id=user.user_id,
                        stream=f,
                        filename=session.filename,
                        content_type=session.content_type,
                        size=session.total_size,
                    )

            await run_in_threadpool(_upload_complete_file)
        finally:
            try:
                media.cancel_upload_session(session_id, user.user_id)
            except Exception as cancel_error:
                # The primary error below is what the client needs.
                logger.debug(f"upload session cancel failed: {cancel_error}")

        logger.info(
            f"User {user.user_id} completed upload session {session_id} ({session.total_size} bytes)"
        )

        return CompleteUploadResponse(
            success=True,
            size=session.total_size,
            message="Upload complete. File ready for processing.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to complete upload session {session_id} for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/upload/session/{session_id}",
    response_model=SuccessResponse,
    summary="Cancel upload session",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Session not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def cancel_upload_session(
    session_id: str, user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """Cancel an upload session and clean up resources."""
    from src.core import media

    try:
        success = media.cancel_upload_session(session_id, user.user_id)

        if not success:
            logger.warning(
                f"Failed to cancel upload session {session_id} for user {user.user_id} (not found)"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Session not found"}},
            )

        logger.info(f"User {user.user_id} cancelled upload session {session_id}")
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to cancel upload session {session_id} for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/upload/sessions",
    response_model=UploadSessionsResponse,
    summary="Get my upload sessions",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user_upload_sessions(
    limit: int = Query(200, ge=1, le=200, description="Max sessions to return"),
    user: TokenInfo = Depends(get_current_user),
) -> UploadSessionsResponse:
    """Get a bounded page of the current user's active upload sessions."""

    # Access the chunked manager directly
    from src.core import media as media_module

    try:
        manager = media_module._get_chunked_manager()
        sessions = manager.get_user_sessions(user.user_id, limit=limit)
        logger.debug(f"User {user.user_id} retrieved {len(sessions)} upload sessions")

        return UploadSessionsResponse(
            sessions=[
                UploadSessionInfo(
                    session_id=s.id,
                    filename=s.filename,
                    content_type=s.content_type,
                    total_size=s.total_size,
                    uploaded_chunks=s.uploaded_chunks,
                    total_chunks=s.total_chunks,
                    progress_percent=(s.uploaded_chunks / s.total_chunks * 100)
                    if s.total_chunks > 0
                    else 0,
                    status=s.status.value,
                    expires_at=s.expires_at,
                )
                for s in sessions
            ]
        )
    except Exception as e:
        logger.error(
            f"Failed to get upload sessions for user {user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== External URL Proxy ====================


@router.get(
    "/proxy",
    summary="Proxy external content",
    responses={
        200: {
            "description": "The proxied content (images only)",
            "content": {
                "image/jpeg": {},
                "image/png": {},
                "image/gif": {},
                "image/webp": {},
            },
        },
        400: {"model": ErrorResponse, "description": "Invalid or blocked URL"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Proxy not available"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_proxied_content(
    url: str,
    user: TokenInfo = Depends(get_current_user),
) -> Response:
    """
    Fetch and serve external image content through Plexichat.

    The external URL is fetched server-side (with SSRF validation, DNS
    rebinding protection, content-type allow-listing and SVG/magic-byte
    rejection) and served to the client so the origin never learns the
    client's address. Results are cached by the media proxy.
    """
    # A bot token's ``user_id`` is its human owner. Reject bot credentials
    # before charging the owner's ``media:proxy:user:<id>`` bucket.
    require_user_token(user)
    if not url or len(url) > 2048:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid URL"}},
        )

    if not _is_allowed_proxy_url(url):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": 400, "message": "Only http/https URLs are allowed"}
            },
        )

    # SECURITY (fetch-amplification defence): every request for an
    # uncached URL makes the server perform an authenticated outbound
    # fetch of up to the configured body-size cap against an arbitrary
    # host. Without a per-user cap any account can turn the server into
    # a bandwidth-draining fetch amplifier by cycling distinct URLs
    # (the cache only helps repeats). Enforce a per-user fixed-window
    # limit on proxy requests, sized generously so normal rendering
    # traffic is unaffected.
    from src.core.database.cache import check_rate_limit

    _limit, _window = _proxy_rate_limit_params()

    allowed, _remaining = check_rate_limit(
        f"media:proxy:user:{user.user_id}", _limit, _window
    )
    if not allowed:
        logger.warning(f"Media proxy rate limit exceeded for user {user.user_id}")
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": "Too many proxied media requests; retry later",
                }
            },
        )

    try:
        media_mod = api.get_media()
        if not media_mod:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Proxy not available"}},
            )

        data, content_type = await run_in_threadpool(media_mod.get_proxied_content, url)
        # SECURITY: proxied bytes originate from an external host. Serve
        # with nosniff so a crafted payload can never be MIME-sniffed into
        # an executable document type by the browser.
        return Response(
            content=data,
            media_type=content_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )
    except HTTPException:
        raise
    except (ProxyFetchError,) as e:
        logger.warning(f"Proxy fetch failed for {url}: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except (ProxyError, MediaError) as e:
        logger.warning(f"Proxy error for {url}: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(
            f"Unexpected error proxying {url} for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== Compression ====================


@router.get(
    "/compression/status",
    response_model=CompressionStatusResponse,
    summary="Get compression status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_compression_status(
    user: TokenInfo = Depends(get_current_user),
) -> CompressionStatusResponse:
    """Get compression system status."""
    from src.core import media

    try:
        status_data = media.get_compression_status()
        logger.debug(f"User {user.user_id} requested compression status")

        return CompressionStatusResponse(
            enabled=status_data["enabled"],
            image_compression=status_data["image_compression"],
            video_compression=status_data["video_compression"],
            pillow_available=status_data["pillow_available"],
            ffmpeg_available=status_data["ffmpeg_available"],
            ffprobe_available=status_data["ffprobe_available"],
        )
    except Exception as e:
        logger.error(
            f"Failed to get compression status for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
