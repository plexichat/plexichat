"""
Attachment serving route - extracted from app.py.

Owns the GET /api/v1/media/attachments/{filename} endpoint: signature
verification (uid-bound), auth fallback, per-file access checks,
malware-scan gating and range-aware streaming.
"""

import io
import unicodedata
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, status, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool

import utils.logger as logger

from src.core.media._streaming import is_seekable_stream, iter_bounded_chunks
from src.core.security.hardening import content_disposition, sanitize_header_filename

from .config import get_api_config
from .lru_cache import LRUCache
from .schemas.common import ErrorResponse

# Local in-memory cache for media file metadata to avoid redundant DB lookups
_media_metadata_cache = LRUCache(maxsize=1024)

# Derived, request-independent metadata (sanitised header name, inline-safety
# verdict, attachment disposition). These depend only on stable per-file
# values (storage filename, original filename and the resolved content type),
# so recomputing the unicode normalisation, regex sanitisation and content-type
# verdict on every range/chunk request is pure waste.
_media_response_meta_cache = LRUCache(maxsize=1024)

# Immutable-attachment body cache. Uploaded media is content-addressed and
# never mutated in place, so a body keyed by (stored filename, file id) is
# byte-for-byte stable. Crucially, every security gate in the handler below --
# signed-URL verification, authentication, per-file access (IDOR) check and the
# fresh malware-scan/deleted check -- still runs on EVERY request *before* this
# cache is consulted. A hit therefore only removes repeated storage path
# resolution / stat / open syscalls for small files; it never bypasses access
# control. Memory is bounded by both the entry cap and the per-file size cap.
_MEDIA_BODY_MAX_FILE_BYTES = 1024 * 1024
_media_body_cache = LRUCache(maxsize=32)


def _cors_headers(
    origin: Optional[str], allowed_origins: frozenset[str], allow_any_origin: bool
) -> dict[str, str]:
    """Build the manual CORS headers from a policy precomputed at registration.

    Kept identical to the old inline logic: an explicit origin (or a wildcard
    policy with a request Origin) echoes the origin and sends credentials;
    a wildcard policy with no Origin sends ``*`` alone.
    """
    cors_headers: dict[str, str] = {}
    if origin and (origin in allowed_origins or allow_any_origin):
        cors_headers["Access-Control-Allow-Origin"] = origin
        cors_headers["Access-Control-Allow-Credentials"] = "true"
    elif allow_any_origin:
        cors_headers["Access-Control-Allow-Origin"] = "*"
    return cors_headers


def _response_metadata(
    filename: str, original_filename: Optional[str], content_type: Optional[str]
) -> tuple[str, bool, str]:
    """Return ``(safe_name, inline_safe, attachment_disposition)``.

    The result is memoised by the stable per-file values it derives from. The
    security-relevant verdict is unchanged: only browser-renderable
    image/video/audio types (never SVG) are eligible for inline rendering;
    everything else is forced to an attachment download.
    """
    key = (filename, original_filename, content_type)
    cached = _media_response_meta_cache.get(key)
    if cached is not None:
        return cached

    safe_name = sanitize_header_filename(
        unicodedata.normalize("NFKD", original_filename or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    ct_lower = (content_type or "").lower()
    inline_safe = ct_lower.startswith(("image/", "video/", "audio/")) and not (
        ct_lower.startswith("image/svg")
    )
    result = (safe_name, inline_safe, content_disposition(safe_name))
    _media_response_meta_cache[key] = result
    return result


def _signed_uid_from_query(query_string: str) -> Optional[str]:
    """Return the ``uid`` value that the URL signer actually validated.

    ``UrlSigner.verify_url`` reads the FIRST ``uid`` value via
    ``parse_qs(query).get("uid", [None])[0]`` and drops *every* ``uid``
    parameter before recomputing the HMAC. Starlette's
    ``request.query_params.get("uid")`` instead returns the LAST value. If the
    two disagree, appending a second ``uid`` keeps the signature valid while
    the route compares a different id to the session user and silently skips
    the per-file access re-check — letting a caller ride an unexpired
    uid-bound URL after their channel access was revoked. Mirror the
    verifier's first-value semantics so the two always agree.
    """
    from urllib.parse import parse_qs

    values = parse_qs(query_string).get("uid", [])
    return values[0] if values else None


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
    # The config snapshot is already captured once at registration; freeze the
    # origin policy too so request handling does no config/list work at all.
    allowed_origins = frozenset(config.cors_origins)
    allow_any_origin = "*" in allowed_origins

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

        # SECURITY: Prevent path traversal. Filenames must be a single segment
        # with no traversal markers, separators or NUL bytes. Reject immediately
        # before touching media/auth.
        if (
            not filename
            or ".." in filename
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
        ):
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
                    signed_uid_raw = _signed_uid_from_query(request.url.query)
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
                # Warm path: reuse an already-validated small body. The auth,
                # per-file access (IDOR) and fresh scan/deleted checks above all
                # ran on this request, so a hit skips only storage syscalls.
                body_key = (filename, file_id)
                cached_body = _media_body_cache.get(body_key)
                if cached_body is not None:
                    body_bytes, ct, size = cached_body
                    stream = io.BytesIO(body_bytes)
                else:
                    # Optimized: Use metadata we already have to skip DB lookup
                    stream, size, ct = media.get_file_stream_optimized(
                        row["storage_path"],
                        row["content_type"],
                        row["storage_backend"],
                    )
                    # Materialise only small, seekable sources: this bounds
                    # memory and preserves range semantics for push streams.
                    # The original handle is closed as soon as its bytes are
                    # captured so no file descriptor is leaked.
                    if size <= _MEDIA_BODY_MAX_FILE_BYTES and is_seekable_stream(
                        stream
                    ):
                        try:
                            stream.seek(0)
                            body_bytes = stream.read(size)
                        finally:
                            try:
                                stream.close()
                            except Exception:
                                pass
                        if len(body_bytes) == size:
                            _media_body_cache[body_key] = (body_bytes, ct, size)
                        stream = io.BytesIO(body_bytes)
                        size = len(body_bytes)

                # Derived, request-independent response metadata. The inline
                # verdict is intentionally based on ``ct`` -- the type the
                # storage layer actually returns -- not the DB row value.
                _, inline_safe, attachment_disposition = _response_metadata(
                    filename, original_filename, ct
                )

                # Generate ETag from file_id and size
                etag = f'"{file_id}-{size}"'

                # Check If-None-Match
                if_none_match = request.headers.get("If-None-Match")
                if if_none_match and (
                    if_none_match == etag or if_none_match == etag.replace('"', "")
                ):
                    return Response(status_code=304)

                is_seekable = is_seekable_stream(stream)
                range_header = request.headers.get("Range") if is_seekable else None
                start, end, is_partial = _parse_byte_range(range_header, size)

                content_length = end - start + 1
                status_code = 206 if is_partial else 200

                # Add CORS headers from the policy frozen at registration.
                cors_headers = _cors_headers(
                    request.headers.get("Origin"), allowed_origins, allow_any_origin
                )

                # Only browser-renderable media types are served inline (the
                # verdict is cached per file); everything else is forced to an
                # attachment so a mis-declared HTML/SVG payload cannot render
                # under our origin.
                disposition = (
                    attachment_disposition
                    if (download or not inline_safe)
                    else "inline"
                )

                headers = {
                    "Content-Disposition": disposition,
                    # Even with a safe Content-Disposition, a browser may sniff
                    # the body and render a mis-declared HTML/SVG payload under
                    # our origin. Pin the declared type for every attachment,
                    # including the inline image/video/audio case.
                    "X-Content-Type-Options": "nosniff",
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

                # One bounded synchronous iterator drives the whole body:
                # chunk sizing, range skipping and zero-copy slicing all run
                # inside the threadpool instead of on the event loop, and the
                # source stream is closed even on an early client disconnect.
                body = iter_bounded_chunks(stream, start, content_length)
                response_iterator = iterate_in_threadpool(body)
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
