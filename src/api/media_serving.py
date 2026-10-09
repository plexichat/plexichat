"""
Attachment serving route - extracted from app.py.

Owns the GET /api/v1/media/attachments/{filename} endpoint: signature
verification (uid-bound), auth fallback, per-file access checks,
malware-scan gating and range-aware streaming.
"""

import re
import unicodedata
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, Request, HTTPException, status, Response
from fastapi.responses import StreamingResponse

import utils.logger as logger

from .config import get_api_config
from .lru_cache import LRUCache
from .schemas.common import ErrorResponse

# Local in-memory cache for media file metadata to avoid redundant DB lookups
_media_metadata_cache = LRUCache(maxsize=1024)


def _parse_byte_range(range_header: Optional[str], size: int) -> tuple[int, int, bool]:
    """Parse a single ``bytes=`` Range header into ``(start, end, is_partial)``.

    ``end`` is inclusive. HTTP suffix ranges (``bytes=-N``) mean the *final* N
    bytes, not the first N: the previous inline parser read ``bytes=-500`` as
    ``0-500`` and served the head of the file behind a falsified
    ``Content-Range``. Anything unsupported or malformed (multi-range,
    unparsable) falls back to the full body so the caller answers 200,
    mirroring :func:`src.static_client.router._parse_range`.
    """
    full = (0, size - 1, False)
    if not range_header or not range_header.startswith("bytes="):
        return full
    spec = range_header[len("bytes=") :].strip()
    # Multi-range requests are unsupported: serve the full body.
    if "," in spec:
        return full
    try:
        if spec.startswith("-"):
            # Suffix range: the final ``n`` bytes (``bytes=-n``).
            n = int(spec[1:])
            if n <= 0 or size <= 0:
                return full
            return max(0, size - n), size - 1, True
        first, _, last = spec.partition("-")
        start = int(first) if first else 0
        end = min(int(last) if last else size - 1, size - 1)
        if start <= end:
            return start, end, True
        return full
    except ValueError:
        return full


def register_attachment_route(app: FastAPI) -> None:
    """Register the attachment-serving endpoint on ``app``."""
    config = get_api_config()

    @app.get(
        "/api/v1/media/attachments/{filename:path}",
        summary="Serve attachment",
        tags=["Media"],
        responses={
            200: {
                "content": {"application/octet-stream": {}},
                "description": "The attachment file content",
            },
            307: {"description": "Redirect to S3 storage"},
            401: {"model": ErrorResponse, "description": "Not authenticated"},
            403: {"model": ErrorResponse, "description": "Access denied"},
            404: {"model": ErrorResponse, "description": "File not found"},
            500: {"model": ErrorResponse, "description": "Internal server error"},
        },
    )
    async def serve_attachment(filename: str, request: Request):
        """Serve uploaded attachment files. Handles local and S3 storage with redirect optimization."""
        import src.api as api_module

        # SECURITY: Prevent path traversal. Filenames must be a single segment.
        # If a traversal attempt is made, reject immediately before touching media/auth.
        if not filename or ".." in filename or "/" in filename or "\\" in filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid filename"}},
            )

        try:
            token = None
            token_info = None
            # --- Signature Verification (Bypass Auth) ---
            media = api_module.get_media()
            if not media:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {"code": 500, "message": "Media module unavailable"}
                    },
                )

            is_signed = False
            try:
                # Check if URL has valid signature
                # Extract path and query for consistent verification with sign_url
                url_to_verify = request.url.path
                if request.url.query:
                    url_to_verify += f"?{request.url.query}"

                # SECURITY: Try to get user_id from session/cookies to verify signed URL ownership
                current_user_id = None
                from .middleware.authentication import get_token_info

                token = request.headers.get("Authorization")
                if token and token.startswith("Bearer "):
                    token = token[7:]
                else:
                    token = request.cookies.get("plexichat_token")

                if token:
                    token_info = await get_token_info(token)
                    if token_info:
                        current_user_id = token_info.user_id

                # ``media.verify_signed_url`` is synchronous (it returns
                # ``Tuple[bool, int]``); awaiting it raises TypeError on the
                # tuple, which the broad handler below swallows — silently
                # disabling signed URLs. Do not add ``await`` here.
                sig_valid, _ = media.verify_signed_url(
                    url_to_verify, current_user_id=current_user_id
                )
                if sig_valid:
                    is_signed = True
                    # SECURITY: uid-bound signatures (uid param embedded by
                    # sign_url) must ALSO pass the per-file access check so
                    # a user whose channel access was revoked cannot ride an
                    # unexpired URL. Intentionally public assets carry no
                    # uid and keep working anonymously.
                    signed_uid_raw = request.query_params.get("uid")
                    if (
                        is_signed
                        and signed_uid_raw
                        and current_user_id is not None
                        and int(signed_uid_raw) == int(current_user_id)
                    ):
                        try:
                            admin_mod = api_module.get_admin()
                            privileged = False
                            if admin_mod and token:
                                privileged = bool(admin_mod.validate_session(token))
                        except Exception:
                            privileged = False
                        if not privileged and not media.check_file_access(
                            filename, int(current_user_id)
                        ):
                            logger.warning(
                                f"Signed URL uid match but per-file access denied: "
                                f"file={filename}, user={current_user_id}"
                            )
                            raise HTTPException(
                                status_code=status.HTTP_403_FORBIDDEN,
                                detail={
                                    "error": {
                                        "code": 403,
                                        "message": "Access denied to this file",
                                    }
                                },
                            )
                    logger.debug(f"Access granted via signed URL: {filename}")
            except HTTPException:
                raise
            except Exception as e:
                logger.debug(f"Signed URL verification failed for {filename}: {e}")

            # --- Authentication (Required if no valid signature or internal) ---
            is_internal = request.scope.get("state", {}).get("is_internal", False)

            if is_internal and not is_signed:
                logger.info(f"Auth: Internal service access to media file: {filename}")

            if not is_signed and not is_internal:
                # Try Authorization header first (preferred for API calls)
                auth_header: Optional[str] = request.headers.get("Authorization")
                token: Optional[str] = None

                if auth_header and auth_header.startswith("Bearer "):
                    token = auth_header[7:]

                # Fallback to cookie-based auth for <img> tags and direct browser access
                if not token:
                    token = request.cookies.get("plexichat_token")

                if not token:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={
                            "error": {"code": 401, "message": "Authentication required"}
                        },
                    )

                # Verify token
                auth = api_module.get_auth()
                if not auth:
                    raise HTTPException(
                        status_code=500,
                        detail={
                            "error": {"code": 500, "message": "Auth module unavailable"}
                        },
                    )

                try:
                    # SECURITY: Use hardened utility to extract client IP from trusted proxies.
                    from src.utils.net import get_client_ip

                    client_ip = get_client_ip(request)

                    user_agent = request.headers.get("User-Agent")
                    token_info = auth.verify_token(token, client_ip, user_agent)
                    if not token_info:
                        raise ValueError("Invalid user token")
                except Exception as e:
                    # Fallback: Check if it's a valid ADMIN token
                    # This allows the Admin Dashboard to load user attachments without 401 errors
                    try:
                        admin = api_module.get_admin()
                        if admin and admin.validate_session(token):
                            # Valid admin session - allow access
                            pass
                        else:
                            logger.warning(
                                f"Attachment auth failed for {filename}: {e}"
                            )
                            raise HTTPException(
                                status_code=401,
                                detail={
                                    "error": {"code": 401, "message": "Invalid token"}
                                },
                            )
                    except Exception:
                        raise HTTPException(
                            status_code=401,
                            detail={"error": {"code": 401, "message": "Invalid token"}},
                        )

            # --- Authorization Check ---
            # Verify user has permission to access this specific file (uploader or participant)
            if not is_signed and not is_internal:
                # Admins and internal services can bypass granular checks
                is_privileged = False
                try:
                    admin_mod = api_module.get_admin()
                    if admin_mod and admin_mod.validate_session(token):
                        is_privileged = True
                except Exception as e:
                    logger.debug(f"Admin session validation failed for {filename}: {e}")

                if not is_privileged:
                    # Resolve user_id from token info
                    if token_info is None:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={
                                "error": {
                                    "code": 401,
                                    "message": "Authentication required",
                                }
                            },
                        )
                    uid_raw = getattr(token_info, "user_id", None)
                    if uid_raw and not media.check_file_access(filename, int(uid_raw)):
                        logger.warning(
                            f"Unauthorized attachment access blocked: file={filename}, user={uid_raw}"
                        )
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail={
                                "error": {
                                    "code": 403,
                                    "message": "Access denied to this file",
                                }
                            },
                        )

            # --- File Lookup ---
            db = api_module.get_db()
            if not db:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {"code": 500, "message": "Database module unavailable"}
                    },
                )

            # Find file in database (check cache first)
            row = _media_metadata_cache.get(filename)
            if not row:
                logger.debug(f"Media cache miss for {filename}, querying database...")
                row = db.fetch_one(
                    "SELECT id, storage_backend, storage_path, content_type, original_filename FROM media_files WHERE filename = ? AND deleted = 0",
                    (filename,),
                )
                if row:
                    logger.debug(
                        f"Found media file in DB: {filename} (ID: {row['id']})"
                    )
                    _media_metadata_cache[filename] = row
                else:
                    logger.warning(f"Media file NOT FOUND in DB: {filename}")

            if not row:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "File not found"}},
                )

            # Security: block access until malware scan completes or confirms clean.
            # The deleted flag is re-checked here on every request so a cached
            # metadata row can never keep serving a file after deletion (the
            # metadata LRU is not invalidated by the delete path).
            scan_row = db.fetch_one(
                "SELECT scan_status, deleted FROM media_files WHERE id = ?",
                (row["id"],),
            )
            if not scan_row or scan_row.get("deleted"):
                _media_metadata_cache.pop(filename, None)
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "File not found"}},
                )
            scan_status = scan_row["scan_status"] if scan_row else ""
            # Positive allowlist: a file is servable only once the scan has
            # confirmed it CLEAN, or when scanning is disabled by policy and
            # the file was therefore recorded SKIPPED. Every other state is
            # blocked. A denylist of ("pending", "infected") previously let
            # "error" (scan failed) and a stray "skipped" written by an
            # unreachable scanner fall through and serve unscanned files.
            if scan_status not in ("clean", "skipped"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "File not available"
                            if scan_status == "pending"
                            else "File blocked by security scan",
                        }
                    },
                )

            file_id = row["id"]
            original_filename = row["original_filename"]
            download = request.query_params.get("download", "0") == "1"

            # --- File Retrieval: Stream via media module ---
            # We always stream through the server now to ensure:
            # 1. Decryption works correctly (EncryptedStorage handles this)
            # 2. Authentication works (we use our own session/cookie)
            # 3. CORS/AccessDenied issues with direct S3 redirects are avoided
            try:
                # Optimized: Use metadata we already have to skip DB lookup
                stream, size, ct = media.get_file_stream_optimized(
                    row["storage_path"], row["content_type"], row["storage_backend"]
                )

                # Sanitize original filename for Content-Disposition
                # This ensures downloads retain their initial names while being safe for headers
                def sanitize_header_filename(name):
                    # Normalize and remove non-ascii
                    name = (
                        unicodedata.normalize("NFKD", name)
                        .encode("ascii", "ignore")
                        .decode("ascii")
                    )
                    # Replace potentially problematic characters
                    name = re.sub(r"[^\w\.\-]", "_", name)
                    return name or "attachment"

                safe_name = sanitize_header_filename(original_filename)

                # Generate ETag from file_id and size
                etag = f'"{file_id}-{size}"'

                # Check If-None-Match
                if_none_match = request.headers.get("If-None-Match")
                if if_none_match and (
                    if_none_match == etag or if_none_match == etag.replace('"', "")
                ):
                    return Response(status_code=304)

                import inspect

                is_seekable = (
                    not inspect.isgenerator(stream)
                    and hasattr(stream, "seek")
                    and hasattr(stream, "tell")
                )
                range_header = request.headers.get("Range") if is_seekable else None
                start, end, is_partial = _parse_byte_range(range_header, size)

                content_length = end - start + 1
                status_code = 206 if is_partial else 200

                # Add CORS headers manually for maximum reliability
                origin = request.headers.get("Origin")
                cors_headers = {}
                allowed_origins = config.cors_origins
                if origin and (origin in allowed_origins or "*" in allowed_origins):
                    cors_headers["Access-Control-Allow-Origin"] = origin
                    cors_headers["Access-Control-Allow-Credentials"] = "true"
                elif "*" in allowed_origins:
                    cors_headers["Access-Control-Allow-Origin"] = "*"

                # Only serve browser-renderable media types inline; everything
                # else (documents, unknown/other, text/*) is forced to
                # attachment so a mis-declared HTML/SVG payload cannot render
                # under our origin.
                ct_lower = (ct or "").lower()
                inline_safe = ct_lower.startswith(
                    ("image/", "video/", "audio/")
                ) and not ct_lower.startswith("image/svg")
                disposition = (
                    f'attachment; filename="{safe_name}"'
                    if (download or not inline_safe)
                    else "inline"
                )

                headers = {
                    "Content-Disposition": disposition,
                    "Cache-Control": "private, max-age=3600",
                    "ETag": etag,
                    "Accept-Ranges": "bytes",
                    "Vary": "Origin, Range",
                    "Access-Control-Expose-Headers": "Content-Range, Accept-Ranges, Content-Length, ETag",
                    **cors_headers,
                }
                if is_seekable:
                    headers["Content-Length"] = str(content_length)

                if is_partial:
                    headers["Content-Range"] = f"bytes {start}-{end}/{size}"

                # Helper to correctly slice any stream (file or generator)
                async def get_response_iterator(
                    s: Any, skip: int, limit: int
                ) -> AsyncGenerator[bytes, None]:
                    import inspect
                    from starlette.concurrency import (
                        iterate_in_threadpool,
                        run_in_threadpool,
                    )

                    count = 0
                    yielded = 0

                    try:
                        if (
                            not inspect.isgenerator(s)
                            and hasattr(s, "read")
                            and hasattr(s, "seek")
                        ):
                            if skip > 0:
                                await run_in_threadpool(s.seek, skip)
                            while yielded < limit:
                                chunk = await run_in_threadpool(
                                    s.read, min(65536, limit - yielded)
                                )
                                if not chunk:
                                    break
                                yield chunk
                                yielded += len(chunk)
                            if hasattr(s, "close"):
                                await run_in_threadpool(s.close)
                        else:
                            from typing import cast

                            async for chunk in iterate_in_threadpool(s):
                                chunk = cast(bytes, chunk)
                                chunk_len = len(chunk)
                                if count + chunk_len <= skip:
                                    count += chunk_len
                                    continue
                                chunk_start = max(0, skip - count)
                                chunk_end = min(
                                    chunk_len, chunk_start + (limit - yielded)
                                )
                                if chunk_start < chunk_end:
                                    part = chunk[chunk_start:chunk_end]
                                    yield part
                                    yielded += len(part)
                                count += chunk_len
                                if yielded >= limit:
                                    break
                    except Exception as e:
                        logger.error(f"Media stream interrupted for {filename}: {e}")

                response_iterator = get_response_iterator(stream, start, content_length)
                return StreamingResponse(
                    response_iterator,
                    status_code=status_code,
                    media_type=ct,
                    headers=headers,
                )
            except Exception as e:
                logger.error(f"Generic retrieval failed for {filename}: {e}")
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": "Retrieval failed"}},
                )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error serving attachment {filename}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Internal server error"}},
            )
