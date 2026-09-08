"""
Artifact route helpers - permission, scope, and response assembly.

Extracted from ``routes/artifacts.py`` to keep each module focused (and under
reviewable size); the symbols are re-exported by ``routes/artifacts.py`` so
existing import paths remain stable.
"""

import json
from typing import Any, Dict, List, Optional

import utils.logger as logger
from fastapi import HTTPException, status

import src.api as api
import utils.config as config
from src.api.schemas.artifacts import ArtifactResponse
from src.core.artifacts.capabilities import (
    artifact_type_capability,
    get_capability,
    is_usable,
)
from src.core.artifacts.models import ArtifactType


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


def payload_revision(payload: Optional[Dict[str, Any]]) -> Optional[int]:
    """Extract an editor's monotonic ``rev`` from a payload dict.

    The code/text artifact editor stamps ``rev`` into the payload on every
    autosave and the REST response echoes the merged payload, so the durable
    revision lives under ``payload["rev"]``. Returns ``None`` when the payload
    carries no usable integer ``rev`` (legacy payloads, other artifact types
    that may use the key differently, or non-dict payloads); the rev guard
    only applies when both sides expose one.
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("rev")
    # Booleans are ints in Python; exclude them so a ``True`` flag cannot
    # masquerade as revision 1.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def stale_payload_write(
    persisted_payload: Optional[Dict[str, Any]],
    incoming_payload: Optional[Dict[str, Any]],
) -> bool:
    """Return True when ``incoming_payload`` is not a newer revision.

    An editor write whose ``rev`` is *not* strictly newer than the persisted
    ``rev`` is stale: a concurrent tab already committed a later revision, and
    letting the older payload through would silently overwrite the newer one
    (lost update). The comparison is ``incoming <= persisted`` so an equal
    rev from a racing tab is rejected too — the editor ignores equal-rev WS
    ops, so allowing equal-rev REST writes would let a second tab's content
    win while the first tab's UI silently diverges from the persisted state.

    Only enforced when BOTH payloads carry a numeric ``rev``; writes without
    one (other artifact types, non-editor updates) are never stale, so the
    guard cannot break payloads that happen to use the key differently.
    """
    incoming = payload_revision(incoming_payload)
    persisted = payload_revision(persisted_payload)
    if incoming is None or persisted is None:
        return False
    return incoming <= persisted


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

    The payload uses the FLAT family (``author_id``/``author_username``/
    ``created_at`` at the top level) via the shared builder in
    :mod:`src.core.events.message_events` — the same shape live CRUD
    broadcasts use — so own-message detection, typing-clear and renderers
    work for artifact messages too.
    """
    try:
        from src.core import events
        from src.core.events import build_flat_message_create

        if not events.is_setup():
            return

        server_id = artifact.server_id
        channel_id = artifact.channel_id
        conversation_id = artifact.conversation_id

        content = (
            f"{config.get('artifacts', {}).get('inline_icon', '📎')} {artifact.title}"
        )

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

        event = build_flat_message_create(
            message_id=artifact.id,
            channel_id=int(channel_id) if channel_id else 0,
            author_id=artifact.author_id,
            content=content,
            author_username=author.get("username"),
            author_avatar_url=author.get("avatar_url"),
            metadata={"artifact_id": str(artifact.id), "message_type": "artifact"},
            server_id=int(server_id) if server_id else None,
            extra={"message_type": "artifact"},
        )

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
