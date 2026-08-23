"""
Artifact routes - REST API for the Artifacts feature.

Implements CRUD + listing for artifacts plus inline transcript emission.
Permission checks defer to the server RBAC layer (`artifact.view/create/
edit/delete/manage_retention`); for DM/group conversations (no server) the
caller must be a participant/owner of the conversation.
"""

import html as _html
import json
import re
from io import BytesIO
from typing import Any, Dict, List, Optional

import utils.logger as logger
import utils.config as config
from fastapi import APIRouter, HTTPException, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SuccessResponse
from fpdf import FPDF
from odf.opendocument import OpenDocumentText
from odf.text import P
from src.core.artifacts.models import ArtifactType, ArtifactStatus
from src.core.artifacts.capabilities import (
    artifact_type_capability,
    get_capability,
    is_usable,
)
from src.api.schemas.artifacts import (
    ArtifactCreateRequest,
    ArtifactUpdateRequest,
    ArtifactResponse,
    ArtifactListResponse,
    ConvertUploadRequest,
)

router = APIRouter(prefix="/artifacts", tags=["Artifacts"])

ARTIFACT_ICON = config.get("artifacts", {}).get("inline_icon", "📎")

# === Permission helpers ===


def _require_server_permission(
    user_id: int, server_id: Optional[int], permission: str
) -> bool:
    """Return True if the user holds ``permission`` in ``server_id``.

    Treats a missing server module or unknown permission name as a deny rather
    than an error, which keeps the route usable before RBAC config is wired.
    """
    if server_id is None:
        return False
    servers_mod = api.get_servers()
    if servers_mod is None:
        return False
    from src.core.servers.exceptions import PermissionDeniedError

    try:
        servers_mod.require_permission(user_id, server_id, permission)
        return True
    except PermissionDeniedError:
        return False
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"server permission check failed for {permission}: {e}")
        return False


def _is_conversation_member(
    messaging_mod: Any, conversation_id: Optional[int], user_id: int
) -> bool:
    """Return True if ``user_id`` participates in / owns the conversation."""
    if conversation_id is None or messaging_mod is None:
        return False
    try:
        return messaging_mod.is_participant(conversation_id, user_id)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"membership check failed for conv {conversation_id}: {e}")
        return False


def _conversation_server_id(conversation_id: Optional[int]) -> Optional[int]:
    """Return the server_id a conversation is bound to, or ``None``."""
    if conversation_id is None:
        return None
    db = api.get_db()
    if db is None:
        return None
    row = db.fetch_one(
        "SELECT metadata FROM msg_conversations WHERE id = ? AND deleted = 0",
        (conversation_id,),
    )
    if not row or not row.get("metadata"):
        return None
    try:
        parsed = json.loads(row["metadata"])
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("server_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _authorize_scope(
    user_id: int,
    conversation_id: Optional[int],
    server_id: Optional[int],
    permission: str,
    author_id: Optional[int] = None,
) -> None:
    """Authorize a server-scoped, conversation-scoped, or personal action.

    Raises HTTPException(403) when the caller has neither the server permission,
    conversation membership, nor (for personal/notes-scope artifacts) authored
    the artifact.

    ``author_id`` is the id of the artifact's author (or the caller's own id
    when creating/listings their personal scope). A personal scope (both
    ``server_id`` and ``conversation_id`` are ``None``) is only accessible to
    the author, matching the WebSocket subscribe handler's behavior.
    """
    if _require_server_permission(user_id, server_id, permission):
        return
    messaging_mod = api.get_messaging()
    if _is_conversation_member(messaging_mod, conversation_id, user_id):
        # Membership alone must not grant access to an unrelated server scope:
        # the conversation must actually be bound to the requested server (or
        # the request is unscoped).
        conv_server = _conversation_server_id(conversation_id)
        if server_id is None or conv_server is None or conv_server == int(server_id):
            return
    # Author-only access applies to the personal scope only (no server and no
    # conversation). A server- or conversation-scoped artifact is authorized
    # by permission/membership above, never by authorship alone: otherwise any
    # caller creating an artifact could pass ``author_id`` equal to their own
    # id and bypass every scope check.
    if (
        author_id is not None
        and int(author_id) == int(user_id)
        and server_id is None
        and conversation_id is None
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": {"code": 403, "message": "Not authorized"}},
    )


def _deep_merge_payload(
    base: Optional[Dict[str, Any]], patch: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge ``patch`` into ``base`` (both dicts) with PATCH semantics.

    Nested dicts are merged recursively; lists and scalars replace the base
    value wholesale; an explicit ``None`` value deletes the key from the
    result. This lets editors persist a partial payload (e.g. only ``content``)
    without clobbering concurrently-updated keys such as ``rev``/``language``.
    """
    result = dict(base or {})
    if not isinstance(patch, dict):
        return result
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_payload(result[key], value)
        else:
            result[key] = value
    return result


# === Manager access ===


def _artifact_capability_for_type(artifact_type: ArtifactType) -> str:
    """Map an artifact type to the capability that gates it."""
    return artifact_type_capability(artifact_type)


def _require_artifact_capability(artifact_type: ArtifactType) -> str:
    """Fail closed when the requested artifact feature is unavailable."""
    feature = _artifact_capability_for_type(artifact_type)
    info = get_capability(feature, config.get("artifacts", {}) or {})
    if not is_usable(info.state):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": 403,
                    "message": f"Artifact feature '{feature}' is unavailable",
                    "state": info.state.value,
                }
            },
        )
    return feature


def _validate_artifact_scope(
    conversation_id: Optional[int],
    channel_id: Optional[int],
    server_id: Optional[int],
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Validate and normalize the artifact's conversation/server/channel scope."""
    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Database unavailable"}},
        )

    if conversation_id is not None:
        row = db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ? AND deleted = 0",
            (conversation_id,),
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Conversation not found"}},
            )
        metadata: Dict[str, Any] = {}
        raw_metadata = row.get("metadata")
        if raw_metadata:
            try:
                parsed = json.loads(raw_metadata)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "error": {
                            "code": 500,
                            "message": "Invalid conversation metadata",
                        }
                    },
                ) from exc
            if isinstance(parsed, dict):
                metadata = parsed
        expected_server = metadata.get("server_id")
        expected_channel = metadata.get("channel_id")
        if expected_server is None and (
            server_id is not None or channel_id is not None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Personal conversation cannot have server/channel scope",
                    }
                },
            )
        if expected_server is not None:
            if server_id is not None and int(server_id) != int(expected_server):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "server_id does not match conversation scope",
                        }
                    },
                )
            server_id = int(expected_server)
        if expected_channel is not None:
            if channel_id is not None and int(channel_id) != int(expected_channel):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "channel_id does not match conversation scope",
                        }
                    },
                )
            channel_id = int(expected_channel)

    if server_id is not None:
        server = db.fetch_one(
            "SELECT id FROM srv_servers WHERE id = ? AND deleted = 0",
            (server_id,),
        )
        if not server:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Server not found"}},
            )

    if channel_id is not None:
        channel = db.fetch_one(
            "SELECT server_id FROM srv_channels WHERE id = ? AND deleted = 0",
            (channel_id,),
        )
        if not channel:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Channel not found"}},
            )
        channel_server = int(channel["server_id"])
        if server_id is not None and int(server_id) != channel_server:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "server_id does not match channel scope",
                    }
                },
            )
        server_id = channel_server

    return conversation_id, channel_id, server_id


def _my_permissions(user_id: int, artifact: Any) -> Dict[str, Any]:
    """Compute authoritative ``can_edit``/``can_view`` for the current user.

    Mirrors ``_authorize_scope`` without raising, so the client renders
    permission-aware UI from server truth instead of guessing at key paths
    (which previously produced misleading "editable" states followed by 403s).
    """
    raw_server = getattr(artifact, "server_id", None)
    raw_conversation = getattr(artifact, "conversation_id", None)
    raw_author = getattr(artifact, "author_id", 0)
    server_id = int(raw_server) if raw_server is not None else None
    conversation_id = int(raw_conversation) if raw_conversation is not None else None
    author_id = int(raw_author) if raw_author is not None else 0

    def _allowed(permission: str) -> bool:
        if _require_server_permission(user_id, server_id, permission):
            return True
        if _is_conversation_member(api.get_messaging(), conversation_id, user_id):
            conv_server = _conversation_server_id(conversation_id)
            if (
                server_id is None
                or conv_server is None
                or conv_server == int(server_id)
            ):
                return True
        return (
            author_id == int(user_id) and server_id is None and conversation_id is None
        )

    return {
        "can_edit": _allowed("artifact.edit"),
        "can_view": _allowed("artifact.view"),
    }


def _author_profile_cache(artifacts: Any) -> Dict[str, Dict[str, Any]]:
    """Bulk-resolve author profiles for a batch of artifacts.

    Returns ``{str(author_id): {"username": ..., "avatar_url": ...}}`` so list
    endpoints avoid an N+1 user lookup. Best-effort: on any failure an empty
    cache is returned and responses simply omit the author fields (the client
    falls back to its own id-based rendering).
    """
    author_ids = sorted(
        {
            int(a_id)
            for a_id in (getattr(a, "author_id", None) for a in artifacts)
            if a_id is not None
        }
    )
    if not author_ids:
        return {}
    auth = api.get_auth()
    if auth is None:
        return {}
    try:
        profiles = auth.get_user_profiles_bulk(author_ids)
    except Exception as e:
        logger.debug(f"Failed to bulk-resolve artifact authors: {e}")
        return {}
    return {
        str(uid): {
            "username": profile.get("username"),
            "avatar_url": profile.get("avatar_url"),
        }
        for uid, profile in (profiles or {}).items()
    }


def _artifact_response(
    user_id: int,
    artifact: Any,
    author_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> ArtifactResponse:
    """Build an artifact response with server-computed permissions attached.

    When ``author_cache`` is not supplied the author profile is resolved for
    this single artifact; batch callers should pass a cache from
    ``_author_profile_cache`` to avoid N+1 lookups.
    """
    response = ArtifactResponse.model_validate(artifact)
    response.my_permissions = _my_permissions(user_id, artifact)

    raw_author_id = getattr(artifact, "author_id", None)
    if raw_author_id is not None:
        if author_cache is None:
            author_cache = _author_profile_cache([artifact])
        profile = author_cache.get(str(raw_author_id)) or {}
        if profile.get("username"):
            response.author_username = profile["username"]
        if profile.get("avatar_url"):
            response.author_avatar_url = profile["avatar_url"]
    return response


def _json_loads(value: Any) -> Any:
    """Parse a JSON string without raising; returns any other value unchanged."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _artifact_scribe_html(payload: Dict[str, Any]) -> str:
    """Return the content HTML of a plexiscribe payload across stored shapes.

    The editor historically stored ``payload.document`` as a serialized JSON
    string while the launcher templates stored it as a dict with a direct
    ``content_html`` key. Both (and a nested ``document.document`` shape) are
    normalized here so exports and text extraction never return raw JSON.
    """
    doc = _json_loads(payload.get("document"))
    if isinstance(doc, dict):
        if isinstance(doc.get("content_html"), str):
            return doc["content_html"]
        inner = doc.get("document")
        if isinstance(inner, dict) and isinstance(inner.get("content_html"), str):
            return inner["content_html"]
    top = payload.get("content_html")
    if isinstance(top, str):
        return top
    return ""


def _artifact_script_text(payload: Dict[str, Any]) -> str:
    """Return the readable source of a plexiscript payload."""
    content = payload.get("content")
    if isinstance(content, str) and content:
        return content
    source = _json_loads(payload.get("source"))
    if isinstance(source, dict):
        for key in ("content", "source", "text"):
            if isinstance(source.get(key), str):
                return source[key]
    if isinstance(source, str):
        return source
    return ""


def _artifact_board_text(payload: Dict[str, Any]) -> str:
    """Return readable text from a whiteboard payload's ``board`` JSON."""
    board = _json_loads(payload.get("board"))
    if not isinstance(board, dict):
        return ""
    parts: List[str] = []
    for element in board.get("text_elements") or []:
        if isinstance(element, dict) and element.get("text"):
            parts.append(str(element["text"]))
    for shape in board.get("shapes") or []:
        if isinstance(shape, dict):
            for key in ("label", "text", "title"):
                if shape.get(key):
                    parts.append(str(shape[key]))
                    break
    for frame in board.get("frames") or []:
        if isinstance(frame, dict) and frame.get("name"):
            parts.append(str(frame["name"]))
    return "\n".join(parts)


def _strip_html_markup(value: str) -> str:
    """Convert HTML to plain text (tags removed, entities decoded)."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = _html.unescape(text).replace("\xa0", " ")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _artifact_export_text(
    artifact_type_value: str, payload: Dict[str, Any], summary: Optional[str]
) -> str:
    """Extract human-readable text for any artifact payload shape."""
    if artifact_type_value == "plexiscribe":
        return _strip_html_markup(_artifact_scribe_html(payload)) or (summary or "")
    if artifact_type_value == "plexiscript":
        return _artifact_script_text(payload)
    if artifact_type_value == "whiteboard":
        return _artifact_board_text(payload) or (summary or "")
    return str(payload.get("content") or payload.get("text") or summary or "")


def _artifact_native_bytes(value: Any) -> bytes:
    """Serialize a native-format export value that may be str, dict, or None."""
    if isinstance(value, str):
        return value.encode("utf-8", "replace")
    if value is None:
        return b"{}"
    return json.dumps(value, ensure_ascii=False).encode("utf-8", "replace")


def _artifact_export_html(
    artifact: Any, artifact_type_value: str, payload: Dict[str, Any]
) -> str:
    """Render a standalone HTML export document."""
    if artifact_type_value == "plexiscribe":
        body = _artifact_scribe_html(payload)
    elif artifact_type_value == "plexiscript":
        body = "<pre>" + _html.escape(_artifact_script_text(payload)) + "</pre>"
    elif artifact_type_value == "whiteboard":
        body = "<pre>" + _html.escape(_artifact_board_text(payload)) + "</pre>"
    else:
        body = _html.escape(str(payload.get("content") or payload.get("text") or ""))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{_html.escape(artifact.title or '')}</title></head>"
        f"<body><h1>{_html.escape(artifact.title or '')}</h1>{body}</body></html>"
    )


def _artifact_export_markdown(artifact: Any, text: str) -> str:
    """Render a Markdown export document."""
    title = _html.escape(artifact.title or "")
    summary = _html.escape(artifact.summary or "")
    if artifact.artifact_type.value == "plexiscript":
        body = "```\n" + text + "\n```"
    else:
        body = text
    return f"# {title}\n\n{summary}\n\n{body}\n"


def _latin1_safe(value: str) -> str:
    """Force text into latin-1 so FPDF core fonts never raise on non-ASCII."""
    return value.encode("latin-1", "replace").decode("latin-1")


def _artifact_export_pdf(artifact: Any, text: str) -> bytes:
    """Render a PDF export using extracted text (latin-1 safe)."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=2.0)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)
    title = _latin1_safe(artifact.title or "Document")
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    for line in _latin1_safe(text or "").splitlines():
        pdf.multi_cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf_bytes = pdf.output()
    return bytes(pdf_bytes) if pdf_bytes else b""


def _artifact_export_odt(artifact: Any, text: str) -> bytes:
    """Render an ODT export using extracted text."""
    doc = OpenDocumentText()
    doc_text = getattr(doc, "text")
    doc_text.addElement(P(text=artifact.title or "Document"))
    doc_text.addElement(P(text=""))
    for line in (text or "").splitlines():
        doc_text.addElement(P(text=line))
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _get_manager():
    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Database unavailable"}},
        )
    from src.core.artifacts.manager import ArtifactManager

    artifacts_cfg = config.get("artifacts", {}) or {}
    return ArtifactManager(db, artifacts_cfg)


# === Inline message emission ===


def _emit_artifact_message(
    artifact: ArtifactResponse,
    author: Dict[str, Any],
) -> None:
    """Emit a MESSAGE_CREATE so the artifact appears in transcript history.

    The message references the artifact via ``metadata.artifact_id`` and uses
    the ``artifact`` message type. Delivery is scoped to the conversation
    participants (preferred) or the owning server; failures are swallowed so
    the underlying artifact write is never rolled back.
    """
    try:
        from src.core import events

        if not events.is_setup():
            return

        server_id = artifact.server_id
        channel_id = artifact.channel_id
        conversation_id = artifact.conversation_id

        content = f"{ARTIFACT_ICON} {artifact.title}"

        messaging_mod = api.get_messaging()
        if messaging_mod is not None and conversation_id is not None:
            try:
                messaging_mod.send_message(
                    user_id=int(artifact.author_id),
                    conversation_id=int(conversation_id),
                    content=content,
                )
            except Exception as e:  # best-effort: never break the artifact write
                logger.debug(f"Failed to persist artifact message: {e}")

        event = events.create_message_create(
            message_id=int(artifact.id),
            channel_id=int(channel_id) if channel_id else 0,
            author_id=int(artifact.author_id),
            content=content,
            server_id=int(server_id) if server_id else None,
            author=author,
        )
        event.data["type"] = 0
        event.data["message_type"] = "artifact"
        event.data["metadata"] = {"artifact_id": str(artifact.id)}

        user_ids: Optional[List[int]] = None
        messaging_mod = api.get_messaging()
        if conversation_id is not None and messaging_mod is not None:
            try:
                user_ids = [
                    int(u) for u in messaging_mod.get_participant_ids(conversation_id)
                ]
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Failed to resolve participants: {e}")
                user_ids = None

        events.dispatch(
            event,
            user_ids=user_ids,
            server_id=int(server_id) if server_id else None,
            channel_id=int(channel_id) if channel_id else None,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Failed to emit artifact message: {e}")


# === Routes ===


@router.post(
    "",
    response_model=ArtifactResponse,
    summary="Create an artifact",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_artifact(
    body: ArtifactCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Create a new artifact and emit an inline transcript message."""
    try:
        conversation_id = (
            int(body.conversation_id) if body.conversation_id is not None else None
        )
        channel_id = int(body.channel_id) if body.channel_id is not None else None
        server_id = int(body.server_id) if body.server_id is not None else None

        conversation_id, channel_id, server_id = _validate_artifact_scope(
            conversation_id, channel_id, server_id
        )
        _authorize_scope(
            current_user.user_id,
            conversation_id,
            server_id,
            "artifact.create",
            author_id=current_user.user_id,
        )

        manager = _get_manager()
        artifact_type = ArtifactType(body.artifact_type.value)
        required_feature = _require_artifact_capability(artifact_type)
        status_enum = ArtifactStatus((body.status or ArtifactStatus.COMPLETED).value)

        artifact = manager.create(
            conversation_id=conversation_id,
            author_id=current_user.user_id,
            artifact_type=artifact_type,
            title=body.title,
            summary=body.summary,
            channel_id=channel_id,
            server_id=server_id,
            status=status_enum,
            recorded=body.recorded,
            has_transcript=body.has_transcript,
            payload=body.payload,
            retention_policy=body.retention_policy,
            # Never trust a client-supplied license label as an authorization
            # decision; persist only the server-resolved capability.
            license_feature=(
                None if required_feature == "artifacts" else required_feature
            ),
        )

        author = {
            "id": str(current_user.user_id),
            "username": current_user.username,
        }
        _emit_artifact_message(ArtifactResponse.model_validate(artifact), author)

        return _artifact_response(current_user.user_id, artifact)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create artifact: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "",
    response_model=ArtifactListResponse,
    summary="List artifacts",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_artifacts(
    conversation_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    server_id: Optional[str] = None,
    author_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    status_filter: Optional[str] = None,
    recorded: Optional[bool] = None,
    has_transcript: Optional[bool] = None,
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_payload: bool = Query(True),
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactListResponse:
    """List artifacts with query filters.

    ``include_payload=False`` omits the (up to 1 MB) type-specific payload
    from every item so metadata-only listings (e.g. the Suite's Scribe/Script/
    Board libraries) never ship full document bodies over the wire. Single-artifact
    GETs and the chat artifacts pane keep ``include_payload=True``.
    """
    try:
        conv_id = int(conversation_id) if conversation_id is not None else None
        chan_id = int(channel_id) if channel_id is not None else None
        srv_id = int(server_id) if server_id is not None else None
        auth_id = int(author_id) if author_id is not None else None

        # Every list request is subject to the base artifacts capability,
        # including untyped listings.
        _require_artifact_capability(ArtifactType.UPLOAD)
        if conv_id is not None or chan_id is not None or srv_id is not None:
            conv_id, chan_id, srv_id = _validate_artifact_scope(
                conv_id, chan_id, srv_id
            )

        # An unscoped request is explicitly the caller's personal scope. Do
        # not authorize it as personal and then query all authors.
        scope_author_id = (
            auth_id
            if auth_id is not None
            else (current_user.user_id if conv_id is None and srv_id is None else None)
        )
        _authorize_scope(
            current_user.user_id,
            conv_id,
            srv_id,
            "artifact.view",
            author_id=scope_author_id,
        )
        if (
            auth_id is not None
            and conv_id is None
            and srv_id is None
            and auth_id != current_user.user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Not authorized"}},
            )

        manager = _get_manager()

        types: Optional[List[Any]] = None
        if artifact_type:
            parts = [p.strip() for p in artifact_type.split(",") if p.strip()]
            types = []
            valid_types = {t.value for t in ArtifactType}
            for p in parts:
                if p in valid_types:
                    types.append(ArtifactType(p))
            # Invalid type values are ignored rather than rejected: a list
            # filter should narrow results, not hard-fail on an unknown label.

        filters: Dict[str, Any] = {
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
        }
        if types is not None:
            filters["artifact_type"] = types
        if status_filter is not None:
            valid_statuses = {s.value for s in ArtifactStatus}
            if status_filter in valid_statuses:
                filters["status"] = ArtifactStatus(status_filter)
            # Unknown status values are ignored (treated as no filter).
        if recorded is not None:
            filters["recorded"] = recorded
        if has_transcript is not None:
            filters["has_transcript"] = has_transcript
        if search:
            filters["search"] = search

        # Keep the count query identical to the item query's scope. Pagination
        # and ordering are intentionally left in ``filters`` because the
        # repository ignores them for COUNT while applying the scope keys.
        if conv_id is not None:
            filters["conversation_id"] = conv_id
        if srv_id is not None:
            filters["server_id"] = srv_id
        if chan_id is not None:
            filters["channel_id"] = chan_id
        if auth_id is not None:
            filters["author_id"] = auth_id
        elif conv_id is None and srv_id is None:
            filters["author_id"] = current_user.user_id

        artifacts = manager.list_with_filters(
            filters=filters,
            conversation_id=conv_id,
            server_id=srv_id,
            channel_id=chan_id,
            author_id=(
                auth_id
                if auth_id is not None
                else (
                    current_user.user_id if conv_id is None and srv_id is None else None
                )
            ),
        )
        total = manager.count(filters)

        author_cache = _author_profile_cache(artifacts)
        items = [
            _artifact_response(current_user.user_id, a, author_cache) for a in artifacts
        ]
        if not include_payload:
            for item in items:
                item.payload = {}

        return ArtifactListResponse(
            items=items,
            total=total,
            has_more=(offset + limit) < total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list artifacts: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/convert-upload",
    response_model=ArtifactResponse,
    summary="Convert an upload to an artifact",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Attachment or message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def _convert_upload_impl(
    body: ConvertUploadRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Convert an existing attachment into an artifact."""
    try:
        conversation_id = (
            int(body.conversation_id) if body.conversation_id is not None else None
        )
        channel_id = int(body.channel_id) if body.channel_id is not None else None
        server_id = int(body.server_id) if body.server_id is not None else None

        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Database unavailable"}},
            )

        attachment_id = int(body.attachment_id)
        row = db.fetch_one(
            "SELECT * FROM msg_attachments WHERE id = ? AND deleted = 0",
            (attachment_id,),
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Attachment not found"}},
            )

        attachment = dict(row)
        attachment["attachment_id"] = attachment_id

        max_size_mb = config.get("artifacts", {}).get("max_artifact_size_mb", 200)
        max_size_bytes = max_size_mb * 1024 * 1024
        if attachment.get("size", 0) > max_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": f"Attachment exceeds maximum size of {max_size_mb} MB",
                    }
                },
            )

        msg_row = db.fetch_one(
            "SELECT conversation_id, author_id FROM msg_messages WHERE id = ? AND deleted = 0",
            (attachment["message_id"],),
        )
        if not msg_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Source message not found"}},
            )
        msg_data = dict(msg_row)

        if current_user.user_id != msg_data["author_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Not authorized"}},
            )

        source_conv_id = msg_data["conversation_id"]
        messaging_mod = api.get_messaging()
        if source_conv_id is not None and not _is_conversation_member(
            messaging_mod, source_conv_id, current_user.user_id
        ):
            # Conversation non-members may still convert the upload if they hold
            # the server-level ``artifact.create`` permission (e.g. a server
            # admin acting on a channel they don't personally participate in).
            if not _require_server_permission(
                current_user.user_id, server_id, "artifact.create"
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": "Not authorized"}},
                )

        # The artifact must remain attached to the source message's
        # conversation and its server/channel scope. Accepting arbitrary target
        # IDs lets a caller move an attachment across conversations.
        source_conv_id = msg_data.get("conversation_id")
        if source_conv_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Source message has no conversation",
                    }
                },
            )
        source_conv_id = int(source_conv_id)
        if conversation_id is not None and conversation_id != source_conv_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "conversation_id must match the source message",
                    }
                },
            )
        conversation_row = db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?",
            (source_conv_id,),
        )
        conversation_metadata: Dict[str, Any] = {}
        if conversation_row and conversation_row.get("metadata"):
            try:
                import json

                parsed_metadata = json.loads(conversation_row["metadata"])
                if isinstance(parsed_metadata, dict):
                    conversation_metadata = parsed_metadata
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "error": {
                            "code": 500,
                            "message": "Source conversation metadata is invalid",
                        }
                    },
                )
        source_server_id = conversation_metadata.get("server_id")
        source_channel_id = conversation_metadata.get("channel_id")
        if (
            server_id is not None
            and source_server_id is not None
            and int(server_id) != int(source_server_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "server_id must match the source conversation",
                    }
                },
            )
        if (
            channel_id is not None
            and source_channel_id is not None
            and int(channel_id) != int(source_channel_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "channel_id must match the source conversation",
                    }
                },
            )
        conversation_id = source_conv_id
        server_id = int(source_server_id) if source_server_id is not None else server_id
        channel_id = (
            int(source_channel_id) if source_channel_id is not None else channel_id
        )
        conversation_id, channel_id, server_id = _validate_artifact_scope(
            conversation_id, channel_id, server_id
        )
        # Re-run authorization against the actual source scope after deriving
        # it from the database, rather than authorizing an omitted target scope.
        _authorize_scope(
            current_user.user_id,
            conversation_id,
            server_id,
            "artifact.create",
            author_id=current_user.user_id,
        )

        manager = _get_manager()
        _require_artifact_capability(ArtifactType.UPLOAD)
        artifact = manager.convert_upload_to_artifact(
            attachment=attachment,
            conversation_id=conversation_id,
            author_id=current_user.user_id,
            title=body.title,
            server_id=server_id,
            channel_id=channel_id,
        )

        author = {
            "id": str(current_user.user_id),
            "username": current_user.username,
        }
        _emit_artifact_message(ArtifactResponse.model_validate(artifact), author)

        return _artifact_response(current_user.user_id, artifact)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to convert upload: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{artifact_id}/export",
    response_class=Response,
    summary="Export an artifact",
)
async def _export_artifact_impl(
    artifact_id: str,
    export_format: str = Query("html", alias="export_format"),
    current_user: TokenInfo = Depends(get_current_user),
) -> Response:
    """Export an artifact as a downloadable file in the requested format.

    Supported formats: html, pdf, md, odt, txt, plexiscribe (native),
    plexiscript, plexiboard.

    Returns the rendered bytes directly with the correct ``Content-Type`` and a
    ``Content-Disposition: attachment`` header so browsers offer a real file
    download (rather than a base64-encoded JSON envelope).
    """
    try:
        try:
            aid = int(artifact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
            )

        valid_formats = {
            "html",
            "pdf",
            "md",
            "odt",
            "txt",
            "plexiscribe",
            "plexiscript",
            "plexiboard",
        }
        if export_format not in valid_formats:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": f"Unsupported format '{export_format}'. Supported: {', '.join(sorted(valid_formats))}",
                    }
                },
            )

        manager = _get_manager()
        artifact = manager.get(aid)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.view",
            author_id=artifact.author_id,
        )
        _require_artifact_capability(artifact.artifact_type)

        mime_map = {
            "html": "text/html",
            "pdf": "application/pdf",
            "md": "text/markdown",
            "odt": "application/vnd.oasis.opendocument.text",
            "txt": "text/plain",
            "plexiscribe": "application/vnd.plexichat.plexiscribe",
            "plexiscript": "application/vnd.plexichat.plexiscript",
            "plexiboard": "application/vnd.plexichat.plexiboard",
        }

        title_slug = (
            artifact.title.lower().replace(" ", "-")[:50]
            if artifact.title
            else "export"
        )
        # Ensure the slug is filesystem-safe (strip anything that isn't
        # alphanumeric, dash, or underscore so the Content-Disposition filename
        # is valid across platforms).
        safe_slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in title_slug)
        filename = f"{safe_slug}.{export_format}"

        payload = artifact.payload or {}
        artifact_type_value = artifact.artifact_type.value
        text = _artifact_export_text(artifact_type_value, payload, artifact.summary)

        if (
            export_format == "plexiscribe"
            and artifact.artifact_type.value == "plexiscribe"
        ):
            data = _artifact_native_bytes(payload.get("document"))
        elif (
            export_format == "plexiscript"
            and artifact.artifact_type.value == "plexiscript"
        ):
            data = _artifact_native_bytes(
                payload.get("source") or payload.get("content")
            )
        elif (
            export_format == "plexiboard"
            and artifact.artifact_type.value == "whiteboard"
        ):
            data = _artifact_native_bytes(payload.get("board"))
        elif export_format == "txt":
            data = text.encode("utf-8", "replace")
        elif export_format == "md":
            data = _artifact_export_markdown(artifact, text).encode("utf-8", "replace")
        elif export_format == "html":
            data = _artifact_export_html(artifact, artifact_type_value, payload).encode(
                "utf-8", "replace"
            )
        elif export_format == "pdf":
            data = _artifact_export_pdf(artifact, text)
        elif export_format == "odt":
            data = _artifact_export_odt(artifact, text)
        else:
            data = str(payload).encode("utf-8", "replace")

        content_type = mime_map.get(export_format, "application/octet-stream")
        # ASCII-safe fallback filename for the disposition header, plus a
        # UTF-8 ``filename*`` parameter so non-ASCII titles still resolve.
        ascii_filename = filename.encode("ascii", "ignore").decode("ascii") or filename

        def _chunk_iter() -> Any:
            yield data

        return StreamingResponse(
            _chunk_iter(),
            media_type=content_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_filename}"; '
                    f"filename*=UTF-8''{filename}"
                ),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Export failed"}},
        )


@router.get(
    "/{artifact_id}/ops",
    response_model=Dict[str, Any],
    summary="List an artifact's persisted ops",
)
async def _list_artifact_ops_impl(
    artifact_id: str,
    after_seq: int = Query(0, description="Return only ops with seq > this"),
    limit: int = Query(500, ge=1, le=2000, description="Max ops to return"),
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the ordered ops log for an artifact.

    Lets clients reconnect to a collaborative artifact without a live WebSocket
    by replaying everything that happened after ``after_seq`` (default: all).
    Each entry is ``{seq, op_type, actor_id, created_at, op}``, the same wire
    shape carried inside ``ARTIFACT_SYNC``.
    """
    try:
        try:
            aid = int(artifact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
            )

        manager = _get_manager()
        artifact = manager.get(aid)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.view",
            author_id=artifact.author_id,
        )
        _require_artifact_capability(artifact.artifact_type)

        ops = manager.list_ops(aid, after_seq=max(0, int(after_seq)), limit=int(limit))
        return {"artifact_id": str(aid), "ops": ops}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to list ops for artifact {artifact_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{artifact_id}",
    response_model=ArtifactResponse,
    summary="Get an artifact",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Artifact not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_artifact(
    artifact_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Fetch a single artifact by id."""
    try:
        try:
            aid = int(artifact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
            )

        manager = _get_manager()
        artifact = manager.get(aid)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.view",
            author_id=artifact.author_id,
        )
        _require_artifact_capability(artifact.artifact_type)

        return _artifact_response(current_user.user_id, artifact)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/{artifact_id:int}",
    response_model=ArtifactResponse,
    summary="Update an artifact",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Artifact not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_artifact(
    artifact_id: int,
    body: ArtifactUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Update mutable fields of an artifact."""
    try:
        manager = _get_manager()
        artifact = manager.get(artifact_id)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        _require_artifact_capability(artifact.artifact_type)

        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.edit",
            author_id=artifact.author_id,
        )

        update_fields: Dict[str, Any] = {}
        if body.title is not None:
            update_fields["title"] = body.title
        if body.summary is not None:
            update_fields["summary"] = body.summary
        if body.status is not None:
            update_fields["status"] = ArtifactStatus(body.status.value)
        if body.payload is not None:
            # Merge (not replace) so a partial payload no longer clobbers
            # concurrently-updated keys. Editors that send the full payload
            # dict are unaffected (a superset merge is a no-op replacement).
            existing_payload = (
                artifact.payload if isinstance(artifact.payload, dict) else {}
            )
            update_fields["payload"] = _deep_merge_payload(
                existing_payload, body.payload
            )
        if "payload" in update_fields:
            try:
                merged_size = len(
                    json.dumps(update_fields["payload"], ensure_ascii=False).encode(
                        "utf-8"
                    )
                )
            except (TypeError, ValueError):
                merged_size = -1
            if merged_size > 1024 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "payload exceeds maximum size of 1 MB",
                        }
                    },
                )
        if body.recorded is not None:
            update_fields["recorded"] = body.recorded
        if body.has_transcript is not None:
            update_fields["has_transcript"] = body.has_transcript
        # ``model_fields_set`` distinguishes an omitted optional field from an
        # explicit null, allowing an operator/editor to clear a policy and its
        # computed expiry.
        if "retention_policy" in getattr(body, "model_fields_set", set()):
            update_fields["retention_policy"] = body.retention_policy

        updated = manager.update(artifact_id, **update_fields)
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )
        return _artifact_response(current_user.user_id, updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{artifact_id}",
    response_model=SuccessResponse,
    summary="Delete an artifact",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Artifact not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_artifact(
    artifact_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """Delete an artifact."""
    try:
        try:
            aid = int(artifact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
            )

        manager = _get_manager()
        artifact = manager.get(aid)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.delete",
            author_id=artifact.author_id,
        )

        if not manager.delete(aid):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to delete"}},
            )
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
