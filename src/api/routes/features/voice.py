from src.api.request_guards import MAX_VOICE_UPLOAD, read_upload_capped
from fastapi import APIRouter, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from typing import Optional
import ipaddress
import socket
from urllib.parse import urlparse

import utils.logger as logger
import src.api as api
from src.core.events.snowflakes import stringify_snowflake_ids
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.api.routes.users.helpers import require_user_token
from src.core.security.hardening import (
    HardeningViolation,
    parse_id,
    reject_control_characters,
)
from .common import (
    call_or_raise,
    parse_id as legacy_parse_id,
    raise_bad_request,
    raise_forbidden,
    raise_internal,
    resolve_participant_service,
)

router = APIRouter()

# Session-scoped voice metadata is client supplied. Keep every field small and
# well formed before it reaches storage or the messaging service: a multi-megabyte
# waveform or a forged channel/server identifier is not a legitimate voice note.
MAX_FILENAME_LENGTH = 255
MAX_URL_LENGTH = 2048
MAX_WAVEFORM_LENGTH = 8192


def _voice_snowflake(value, field: str):
    """Validate an optional channel/server identifier via the shared rule."""
    if value is None:
        return None
    try:
        return parse_id(value, field)
    except HardeningViolation as exc:
        raise_bad_request(str(exc))


def _require_voice_send_access(servers, user_id: int, conversation_id: int) -> None:
    """Fail closed when the voice target is a channel the caller cannot post to.

    Voice publishes straight through ``messaging.send_message``, which does not
    apply the REST send route's ``messages.send``, timeout or channel-view
    gates. Conversation participation is seeded for every server member on
    join, so checking it alone lets a member who may only view a read-only
    channel post a voice message into it. A conversation with no backing server
    channel is a genuine DM/group and is already covered by the participation
    gate in ``VoiceMessageService``. Any unresolved permission data denies.
    """
    if not servers:
        # The backing-channel lookup cannot be evaluated without the servers
        # module. A private-channel alias can exist in the DB independently of
        # that module, so skipping this gate would let a member post a voice
        # message into a channel whose messages.send grant was never checked.
        # Deny instead; a wired store with no backing channel still allows a
        # genuine DM below.
        raise PermissionError("Cannot verify channel send access")
    lookup = getattr(servers, "get_channel_by_conversation", None)
    if not callable(lookup):
        raise PermissionError("Cannot verify channel send access")
    try:
        info = lookup(conversation_id)
    except Exception:
        raise PermissionError("Cannot verify channel send access")
    if info is None:
        return  # DM/group conversation: participation already gated it
    channel_id = info.get("id") if isinstance(info, dict) else None
    server_id = info.get("server_id") if isinstance(info, dict) else None
    if channel_id is None or server_id is None:
        raise PermissionError("Cannot verify channel send access")

    # get_channel enforces membership + channels.view for this exact caller.
    get_channel = getattr(servers, "get_channel", None)
    timed_out = getattr(servers, "is_timed_out", None)
    has_permission = getattr(servers, "has_permission", None)
    if get_channel is None or timed_out is None or has_permission is None:
        raise PermissionError("Cannot verify channel send access")
    if not (callable(get_channel) and callable(timed_out) and callable(has_permission)):
        raise PermissionError("Cannot verify channel send access")
    try:
        if get_channel(channel_id, user_id) is None:
            raise PermissionError("Cannot post to this channel")
        if timed_out(user_id, server_id):
            raise PermissionError("Timed out in this server")
        allowed = bool(has_permission(user_id, server_id, "messages.send", channel_id))
    except PermissionError:
        raise
    except Exception:
        raise PermissionError("Cannot verify channel send access")
    if not allowed:
        raise PermissionError("Missing messages.send permission")


def _validate_waveform(value) -> None:
    """Reject oversized or control-character-laden waveform data."""
    if value is None:
        return
    if not isinstance(value, str) or len(value) > MAX_WAVEFORM_LENGTH:
        raise_bad_request("Invalid waveform data")
    try:
        reject_control_characters(value, "waveform")
    except HardeningViolation as exc:
        raise_bad_request(str(exc))


# M3 FIX: the JSON variant previously trusted client-supplied attachment
# metadata verbatim (url/filename/content_type/size), unlike the multipart
# variant which validates server-side. Apply the same class of checks here.
ALLOWED_CONTENT_TYPES = {
    "audio/webm",
    "audio/ogg",
    "audio/mp3",
    "audio/mpeg",
    "audio/wav",
    "audio/x-wav",
    "audio/opus",
}
ALLOWED_EXTENSIONS = {"webm", "ogg", "mp3", "wav", "opus"}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # matches the multipart variant
MAX_DURATION_MS = 600000
ALLOWED_URL_PREFIXES = ("/api/v1/media/", "/media/", "/uploads/")
ALLOWED_URL_SCHEMES = ("http://", "https://")


def _validate_voice_metadata(
    filename: str, content_type: str, size: int, duration_ms: int, url: str
) -> None:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise_bad_request(
            f"Invalid content type '{content_type}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
        )

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise_bad_request(
            f"Unsupported voice format '{ext}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    if size <= 0:
        raise_bad_request("Voice file is empty")
    if size > MAX_UPLOAD_SIZE:
        raise_bad_request("Audio file exceeds 10MB maximum size")

    if duration_ms <= 0:
        raise_bad_request("Duration must be positive")
    if duration_ms > MAX_DURATION_MS:
        raise_bad_request("Voice message exceeds 10 minute maximum duration")

    candidate = url.strip()
    lowered = candidate.lower()
    if lowered.startswith(("javascript:", "data:", "file:", "blob:")):
        raise_bad_request("Invalid storage URL")
    if candidate.startswith(ALLOWED_URL_PREFIXES):
        return
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise_bad_request("Invalid storage URL")
    if parsed.username or parsed.password:
        raise_bad_request("Invalid storage URL")
    # A literal backslash in the authority is a parser differential: urlparse
    # derives the host from the text to the RIGHT of an '@', but browsers and
    # http(s) clients following WHATWG read '\' as '/' and connect to the
    # address on the LEFT. Without this check,
    # ``https://169.254.169.254\@public.example/x`` passes the DNS screening
    # below (as public.example) yet is stored verbatim as a voice attachment
    # URL - so every recipient whose client follows it reaches the link-local
    # metadata address on their own network. A literal backslash has no
    # legitimate place in a URL authority; data must be percent-encoded
    # (``%5C``), which keeps it out of the authority. Mirrors
    # outbound.parse_outbound_url and profiles._ssrf_guard_url.
    if "\\" in (parsed.netloc or ""):
        raise_bad_request("Invalid storage URL")
    # The DNS + IP screening is deliberately NOT done here: see
    # _assert_public_storage_host, which the route awaits off the event loop.


def _assert_public_storage_host(hostname: str) -> None:
    """Resolve `hostname` and reject any non-public address.

    Separate from the metadata checks because it performs blocking I/O.
    """
    try:
        from src.core.security.outbound import (
            OutboundUrlRejected,
            reject_forbidden_ip_literal,
        )

        addresses = [
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        ]
    except (OSError, ValueError):
        raise_bad_request("Invalid storage URL")
    # Use the central SSRF policy instead of an ad-hoc ``is_private`` check:
    # ipaddress flags only a subset of non-global space, and the shared gate
    # additionally rejects RFC 6598 CGNAT (100.64.0.0/10), NAT64 and mapped
    # forms that otherwise resolve to a viewer's internal network.
    if not addresses:
        raise_bad_request("Invalid storage URL")
    for address in addresses:
        try:
            reject_forbidden_ip_literal(str(address), "storage_url")
        except OutboundUrlRejected:
            raise_bad_request("Invalid storage URL")
    # External URLs are accepted only after DNS/IP safety checks. This keeps
    # legacy JSON clients working while preventing loopback/private targets.


async def _validate_voice_storage_url(url: str) -> None:
    """Run the storage-URL safety checks without blocking the event loop.

    ``socket.getaddrinfo`` is a synchronous syscall that can block for the
    full resolver timeout. Calling it directly from this module's ``async def``
    route stalled the entire worker for every concurrent request whenever the
    hostname was slow to resolve -- and the hostname is supplied by the
    client, so a handful of requests pointing at a black-hole DNS name was
    enough to stall a worker. Offloading to the threadpool keeps the loop
    responsive; the media proxy already uses this pattern for the same reason.
    """
    candidate = url.strip()
    if not candidate or candidate.startswith(ALLOWED_URL_PREFIXES):
        return
    parsed = urlparse(candidate)
    if parsed.hostname:
        await run_in_threadpool(_assert_public_storage_host, parsed.hostname)


class VoiceMessageRequest(BaseModel):
    conversation_id: str = Field(..., description="Target conversation ID")
    channel_id: Optional[str] = Field(
        None, description="Optional originating channel ID"
    )
    server_id: Optional[str] = Field(None, description="Optional originating server ID")
    duration_ms: int = Field(
        ..., gt=0, le=MAX_DURATION_MS, description="Duration in milliseconds"
    )
    filename: str = Field(
        ..., min_length=1, max_length=MAX_FILENAME_LENGTH, description="Audio filename"
    )
    content_type: str = Field(..., description="MIME type (audio/*)")
    size: int = Field(..., gt=0, description="File size in bytes")
    url: str = Field(..., max_length=MAX_URL_LENGTH, description="Storage URL")
    waveform: Optional[str] = Field(
        None, max_length=MAX_WAVEFORM_LENGTH, description="Base64 waveform data"
    )

    @field_validator("channel_id", "server_id")
    @classmethod
    def _safe_scope_id(cls, value, info):
        if value is None:
            return value
        try:
            return str(parse_id(value, info.field_name))
        except HardeningViolation as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("filename")
    @classmethod
    def _safe_filename(cls, value: str, info):
        if not value or not value.strip():
            raise ValueError("filename must not be blank")
        try:
            reject_control_characters(value, info.field_name)
        except HardeningViolation as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("waveform")
    @classmethod
    def _safe_waveform(cls, value, info):
        if value is None:
            return value
        if not isinstance(value, str) or len(value) > MAX_WAVEFORM_LENGTH:
            raise ValueError("Invalid waveform data")
        try:
            reject_control_characters(value, info.field_name)
        except HardeningViolation as exc:
            raise ValueError(str(exc)) from exc
        return value


@router.post(
    "/voice-messages",
    summary="Send a voice message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def send_voice_message(
    body: VoiceMessageRequest, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
    conv_id = legacy_parse_id(body.conversation_id, "conversation ID")
    # Validate the optional session scope even though persistence is unchanged;
    # the ids must still be well-formed snowflakes before any downstream use.
    _voice_snowflake(body.channel_id, "channel ID")
    _voice_snowflake(body.server_id, "server ID")
    _validate_waveform(body.waveform)

    _validate_voice_metadata(
        filename=body.filename,
        content_type=body.content_type,
        size=body.size,
        duration_ms=body.duration_ms,
        url=body.url,
    )

    # DNS-backed SSRF screening, run off the event loop.
    await _validate_voice_storage_url(body.url)

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = resolve_participant_service(messaging)

    # Voice publishes through messaging.send_message, bypassing the REST send
    # route's messages.send/timeout/view gates. Enforce them here, fail closed,
    # before any voice message is persisted or published.
    call_or_raise(
        _require_voice_send_access,
        api.get_servers(),
        current_user.user_id,
        conv_id,
    )

    from src.core.messaging.services.voice import VoiceMessageService

    svc = VoiceMessageService(db)

    try:
        result = svc.create_voice_message(
            user_id=current_user.user_id,
            conversation_id=conv_id,
            duration_ms=body.duration_ms,
            filename=body.filename,
            content_type=body.content_type,
            size=body.size,
            url=body.url,
            waveform=body.waveform,
            participant_svc=participant_svc,
        )

        attachment_data = getattr(result, "attachment_data", None)
        if attachment_data is None:
            attachment_data = {
                "filename": result.get("filename")
                if isinstance(result, dict)
                else result.filename,
                "content_type": result.get("content_type")
                if isinstance(result, dict)
                else result.content_type,
                "size": result.get("size") if isinstance(result, dict) else result.size,
                "url": result.get("url") if isinstance(result, dict) else result.url,
                "metadata": result.get("metadata")
                if isinstance(result, dict)
                else result.metadata,
            }

        if messaging:
            try:
                msg = messaging.send_message(
                    user_id=current_user.user_id,
                    conversation_id=conv_id,
                    content="Voice message",
                    attachments=[attachment_data],
                )
                if isinstance(result, dict):
                    result["message_id"] = msg.id
                else:
                    setattr(result, "message_id", msg.id)
            except Exception as e:
                logger.error(f"Failed to send voice message via messaging: {e}")

        return {"success": True, "voice_message": stringify_snowflake_ids(result)}
    except ValueError:
        raise_bad_request("Invalid voice message data")
    except PermissionError:
        # M6 FIX: authorization failures are 403 with a static message,
        # never a 500 echoing the exception text.
        raise_forbidden("Not a participant in this conversation")


@router.post(
    "/voice-messages/upload",
    summary="Upload a voice message audio file",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def upload_voice_message(
    conversation_id: str = Form(..., description="Target conversation ID"),
    duration_ms: int = Form(..., gt=0, description="Duration in milliseconds"),
    audio: UploadFile = File(..., description="Audio file (webm, ogg, mp3, wav, opus)"),
    waveform: Optional[str] = Form(None, description="Base64 waveform data"),
    current_user: TokenInfo = Depends(get_current_user),
):
    require_user_token(current_user)
    allowed_content_types = {
        "audio/webm",
        "audio/ogg",
        "audio/mp3",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/opus",
    }
    if not audio.content_type or audio.content_type not in allowed_content_types:
        raise_bad_request(
            f"Invalid content type. Allowed: {', '.join(sorted(allowed_content_types))}"
        )

    MAX_UPLOAD_SIZE = 10 * 1024 * 1024
    content = await read_upload_capped(audio, MAX_VOICE_UPLOAD)
    if len(content) > MAX_UPLOAD_SIZE:
        raise_bad_request("Audio file exceeds 10MB maximum size")
    if len(content) == 0:
        raise_bad_request("Audio file is empty")

    MAX_DURATION_MS = 600000
    if duration_ms > MAX_DURATION_MS:
        raise_bad_request("Voice message exceeds 10 minute maximum duration")

    _validate_waveform(waveform)
    conv_id = legacy_parse_id(conversation_id, "conversation ID")

    import time

    # The media module is the only thing that may mint an attachment URL. If it
    # is unavailable, fail closed instead of embedding the bytes in a ``data:``
    # URL: the JSON variant explicitly rejects that scheme (active-content sink
    # and unbounded length), and base64-encoding up to 10MB of audio here used
    # to produce a ~13MB ``url`` string stored on the message and echoed on
    # every read, bypassing the JSON path's MAX_URL_LENGTH boundary entirely.
    media = api.get_media()
    if not media:
        raise_internal("Media storage unavailable")

    try:
        ext_map = {
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/mp3": "mp3",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/opus": "opus",
        }
        ext = ext_map.get(audio.content_type, "webm")
        filename = f"voice_{current_user.user_id}_{int(time.time() * 1000)}.{ext}"

        result = media.upload_file(
            file_data=content,
            filename=filename,
            content_type=audio.content_type,
            user_id=current_user.user_id,
        )
        attachment_data = {
            "filename": result.get("filename")
            if isinstance(result, dict)
            else result.filename,
            "content_type": result.get("content_type")
            if isinstance(result, dict)
            else result.content_type,
            "size": result.get("size") if isinstance(result, dict) else result.size,
            "url": result.get("url") if isinstance(result, dict) else result.url,
            "metadata": result.get("metadata")
            if isinstance(result, dict)
            else result.metadata,
        }
        storage_url = attachment_data["url"] if attachment_data["url"] else ""
        file_size = attachment_data["size"] if attachment_data["size"] else len(content)
    except Exception as e:
        logger.error(f"Failed to store voice message audio: {e}")
        raise_internal("Failed to store audio file")

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = resolve_participant_service(messaging)

    # Voice publishes through messaging.send_message, bypassing the REST send
    # route's messages.send/timeout/view gates. Enforce them here, fail closed,
    # before any voice message is persisted or published.
    call_or_raise(
        _require_voice_send_access,
        api.get_servers(),
        current_user.user_id,
        conv_id,
    )

    from src.core.messaging.services.voice import VoiceMessageService

    svc = VoiceMessageService(db)

    try:
        result = svc.create_voice_message(
            user_id=current_user.user_id,
            conversation_id=conv_id,
            duration_ms=duration_ms,
            filename=filename,
            content_type=audio.content_type,
            size=file_size,
            url=storage_url,
            waveform=waveform,
            participant_svc=participant_svc,
        )

        attachment_data = (
            result.get("attachment_data")
            if isinstance(result, dict)
            else getattr(result, "attachment_data", None)
        )
        if messaging and attachment_data:
            try:
                msg = messaging.send_message(
                    user_id=current_user.user_id,
                    conversation_id=conv_id,
                    content="Voice message",
                    attachments=[attachment_data],
                )
                if isinstance(result, dict):
                    result["message_id"] = msg.id
                else:
                    setattr(result, "message_id", msg.id)
            except Exception as e:
                logger.error(f"Failed to send voice message via messaging: {e}")
        return {"success": True, "voice_message": stringify_snowflake_ids(result)}
    except ValueError:
        raise_bad_request("Invalid voice message data")
    except PermissionError:
        # M6 FIX: static 403 instead of raise_internal(str(e)).
        raise_forbidden("Not a participant in this conversation")
