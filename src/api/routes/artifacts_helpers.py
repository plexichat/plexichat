"""
Artifact route helpers - permission, scope, and response assembly.

Extracted from ``routes/artifacts.py`` to keep each module focused (and under
reviewable size); the symbols are re-exported by ``routes/artifacts.py`` so
existing import paths remain stable.
"""

import json
from types import SimpleNamespace
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

# Re-exported for ``routes/artifacts.py`` (stable import surface).
# because the helpers are only consumed by the route module, not here.
from src.core.artifacts.revision import (
    payload_revision as payload_revision,
    stale_payload_write as stale_payload_write,
)


# === Permission helpers ===


class _AuthCache:
    """Per-request memo for repeated authorization lookups.

    A listing previously re-ran the same server-permission check, conversation
    membership check and ``msg_conversations`` metadata query once per artifact
    (and again in ``_authorize_scope``). All three are read-only for the life
    of a request, so one small cache collapses that N+1 into a single lookup
    per distinct scope. It is never shared across requests.
    """

    __slots__ = ("server_permissions", "memberships", "conversation_servers")

    def __init__(self) -> None:
        self.server_permissions: Dict[tuple, bool] = {}
        self.memberships: Dict[tuple, bool] = {}
        self.conversation_servers: Dict[int, Optional[int]] = {}


def _require_server_permission(
    user_id: int,
    server_id: Optional[int],
    permission: str,
    cache: Optional[_AuthCache] = None,
) -> bool:
    """Return True if the user holds ``permission`` in ``server_id``.

    Treats a missing server module or unknown permission name as a deny rather
    than an error, which keeps the route usable before RBAC config is wired.
    """
    if server_id is None:
        return False
    key = (user_id, server_id, permission)
    if cache is not None and key in cache.server_permissions:
        return cache.server_permissions[key]
    result = _require_server_permission_uncached(user_id, server_id, permission)
    if cache is not None:
        cache.server_permissions[key] = result
    return result


def _require_server_permission_uncached(
    user_id: int, server_id: Optional[int], permission: str
) -> bool:
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
    messaging_mod: Any,
    conversation_id: Optional[int],
    user_id: int,
    cache: Optional[_AuthCache] = None,
) -> bool:
    """Return True if ``user_id`` participates in / owns the conversation."""
    if conversation_id is None or messaging_mod is None:
        return False
    key = (conversation_id, user_id)
    if cache is not None and key in cache.memberships:
        return cache.memberships[key]
    try:
        result = bool(messaging_mod.is_participant(conversation_id, user_id))
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"membership check failed for conv {conversation_id}: {e}")
        result = False
    if cache is not None:
        cache.memberships[key] = result
    return result


def _conversation_server_id(
    conversation_id: Optional[int], cache: Optional[_AuthCache] = None
) -> Optional[int]:
    """Return the server_id a conversation is bound to, or ``None``."""
    if conversation_id is None:
        return None
    if cache is not None and conversation_id in cache.conversation_servers:
        return cache.conversation_servers[conversation_id]
    result = _conversation_server_id_uncached(conversation_id)
    if cache is not None:
        cache.conversation_servers[conversation_id] = result
    return result


def _server_id_from_conversation_metadata(raw_metadata: Any) -> Optional[int]:
    """Extract ``server_id`` from a serialized ``msg_conversations.metadata`` blob."""
    if not raw_metadata:
        return None
    try:
        parsed = json.loads(raw_metadata)
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


def _conversation_server_id_uncached(
    conversation_id: Optional[int],
) -> Optional[int]:
    db = api.get_db()
    if db is None:
        return None
    row = db.fetch_one(
        "SELECT metadata FROM msg_conversations WHERE id = ? AND deleted = 0",
        (conversation_id,),
    )
    if not row:
        return None
    return _server_id_from_conversation_metadata(row.get("metadata"))


def _prime_conversation_server_cache(
    artifacts: Any, cache: Optional[_AuthCache] = None
) -> None:
    """Resolve every artifact conversation's ``server_id`` in one bulk read.

    ``_my_permissions`` (and ``_authorize_scope``) resolve the conversation's
    ``metadata.server_id`` through ``_conversation_server_id`` for each artifact
    whose caller is a conversation member. Without priming, a page of N
    artifacts spread across N distinct conversations issues N
    ``msg_conversations`` reads — the N+1 this helper collapses into one
    ``IN (?, ...)`` query. Results are memoized in ``cache.conversation_servers``
    so subsequent per-item lookups are dict hits.

    Best-effort: ids already resolved (e.g. by scope validation) are skipped,
    and any DB failure leaves the cache untouched so the per-item fallback in
    ``_conversation_server_id`` still works exactly as before.
    """
    if cache is None:
        return
    pending = sorted(
        {
            int(cid)
            for cid in (getattr(a, "conversation_id", None) for a in artifacts)
            if cid is not None
        }
    )
    pending = [cid for cid in pending if cid not in cache.conversation_servers]
    if not pending:
        return
    db = api.get_db()
    if db is None:
        return
    try:
        rows = db.fetch_many_by_ids(
            "msg_conversations", pending, columns=["id", "metadata", "deleted"]
        )
    except Exception as e:  # pragma: no cover - defensive; fallback stays correct
        logger.debug(f"Failed to bulk-resolve artifact conversation metadata: {e}")
        return
    for cid in pending:
        row = rows.get(cid)
        if isinstance(row, dict) and not row.get("deleted"):
            cache.conversation_servers[cid] = _server_id_from_conversation_metadata(
                row.get("metadata")
            )
        else:
            # Soft-deleted or missing conversation: mirror the per-item query,
            # which filters ``deleted = 0`` and resolves to None.
            cache.conversation_servers[cid] = None


def _authorize_scope(
    user_id: int,
    conversation_id: Optional[int],
    server_id: Optional[int],
    permission: str,
    author_id: Optional[int] = None,
    cache: Optional[_AuthCache] = None,
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
    if _require_server_permission(user_id, server_id, permission, cache):
        return
    messaging_mod = api.get_messaging()
    if _is_conversation_member(messaging_mod, conversation_id, user_id, cache):
        # Membership alone must not grant access to an unrelated server scope:
        # the conversation must actually be bound to the requested server (or
        # the request is unscoped).
        conv_server = _conversation_server_id(conversation_id, cache)
        # ``conv_server is None`` must NOT be treated as an unscoped request:
        # ``server_id`` is still a real server here. It means the binding could
        # not be resolved (missing/invalid conversation metadata, a
        # soft-deleted conversation, or a DB read failure). Granting access on
        # that basis let a mere conversation participant read/edit a
        # server-scoped artifact without ever holding the server permission --
        # exactly the "unrelated server scope" bypass this gate is meant to
        # stop. Fail closed until the conversation is proven bound to the
        # artifact's server. Only a genuinely unscoped artifact (server_id is
        # None) is authorized by conversation membership alone.
        if server_id is None or (
            conv_server is not None and conv_server == int(server_id)
        ):
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


def _artifact_type_enabled(artifact_type: ArtifactType) -> bool:
    """Whether an artifact type passed the same gate as a single-artifact GET.

    Shared by the untyped listing path so it cannot expose stored types whose
    license/config capability is currently unavailable.
    """
    feature = _artifact_capability_for_type(artifact_type)
    info = get_capability(feature, config.get("artifacts", {}) or {})
    return is_usable(info.state)


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
    cache: Optional[_AuthCache] = None,
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
        # _authorize_scope will ask for this same value shortly; hand it the
        # row we already read instead of issuing a second DB query.
        if cache is not None:
            try:
                cache.conversation_servers[conversation_id] = (
                    int(expected_server) if expected_server is not None else None
                )
            except (TypeError, ValueError):
                pass
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


def _my_permissions(
    user_id: int, artifact: Any, cache: Optional[_AuthCache] = None
) -> Dict[str, Any]:
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
        if _require_server_permission(user_id, server_id, permission, cache):
            return True
        if _is_conversation_member(
            api.get_messaging(), conversation_id, user_id, cache
        ):
            conv_server = _conversation_server_id(conversation_id, cache)
            # An unresolvable conversation->server binding (deleted/invalid
            # metadata or a DB read failure) must NOT be treated as an unscoped
            # request. ``_authorize_scope`` already fails closed here; this
            # permission-reporting helper must mirror it, otherwise a mere
            # conversation participant is told can_view/can_edit for a
            # server-scoped artifact they cannot actually access.
            if server_id is None or (
                conv_server is not None and conv_server == int(server_id)
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
    auth_cache: Optional[_AuthCache] = None,
) -> ArtifactResponse:
    """Build an artifact response with server-computed permissions attached.

    When ``author_cache`` is not supplied the author profile is resolved for
    this single artifact; batch callers should pass a cache from
    ``_author_profile_cache`` to avoid N+1 lookups. ``auth_cache`` likewise
    shares the permission/membership lookups across every item in a listing.
    """
    response = ArtifactResponse.model_validate(artifact)
    response.my_permissions = _my_permissions(user_id, artifact, auth_cache)

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


def _get_artifact_scope(artifact_id: Any) -> Optional[SimpleNamespace]:
    """Load only the columns needed to authorize a read of one artifact.

    ``ArtifactManager.get`` issues ``SELECT *`` and parses the full
    ``payload``/``summary``/``retention_policy`` JSON. Endpoints that only
    replay a side table (such as the ops log) never touch that data, so loading
    it on every request is wasted work that scales with artifact size. This
    focused read keeps the same existence and scope semantics (no ``deleted``
    filter, same legacy ``artifact_type`` fallback) while fetching four columns
    instead of the whole row. Returns ``None`` when the artifact does not exist.
    """
    db = api.get_db()
    if db is None:
        return None
    row = db.fetch_one(
        "SELECT id, conversation_id, server_id, author_id, artifact_type "
        "FROM artifacts WHERE id = ?",
        (artifact_id,),
    )
    if not row:
        return None
    if isinstance(row, dict):
        raw_type = row.get("artifact_type")
        conversation_id = row.get("conversation_id")
        server_id = row.get("server_id")
        author_id = row.get("author_id")
    else:
        # Defensive: positional adapters use this column order.
        (
            _,
            conversation_id,
            server_id,
            author_id,
            raw_type,
        ) = (row[0], row[1], row[2], row[3], row[4])
    try:
        artifact_type = ArtifactType(raw_type)
    except ValueError:
        # Match ``repository._parse_artifact_type``: drift must not crash a read.
        artifact_type = ArtifactType.FUTURE
    return SimpleNamespace(
        id=artifact_id,
        conversation_id=conversation_id,
        server_id=server_id,
        author_id=author_id,
        artifact_type=artifact_type,
    )


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


def _can_emit_artifact_message(conversation_id, author_id) -> bool:
    """Fail closed when the artifact would post into a channel the author cannot.

    ``_emit_artifact_message`` persists a message straight through
    ``messaging.send_message``, whose only gate is conversation participation.
    For a server channel participation is seeded for every member on join and
    is NOT a send grant. Artifact creation is gated by ``artifact.create``,
    which is granted to @everyone by default and says nothing about posting in
    the specific backing channel. Without this guard a member who can view a
    read-only channel -- or who is timed out in the server -- can create an
    artifact and have a message carrying their chosen title posted into a
    channel where the REST send route would reject them. Any permission data
    that cannot be resolved denies the emission.
    """
    if conversation_id is None:
        return True  # no conversation to post into (event-only emission)
    servers = api.get_servers()
    if servers is None:
        return True  # no servers store => conversation can only be a DM/group
    lookup = getattr(servers, "get_channel_by_conversation", None)
    if not callable(lookup):
        return False
    try:
        info = lookup(conversation_id)
    except Exception:
        return False
    if info is None:
        return True  # genuine DM/group: participation is the only boundary
    channel_id = info.get("id") if isinstance(info, dict) else None
    server_id = info.get("server_id") if isinstance(info, dict) else None
    if channel_id is None or server_id is None:
        return False

    get_channel = getattr(servers, "get_channel", None)
    timed_out = getattr(servers, "is_timed_out", None)
    has_permission = getattr(servers, "has_permission", None)
    if not (callable(get_channel) and callable(timed_out) and callable(has_permission)):
        return False
    try:
        # get_channel enforces membership + channels.view for this exact caller.
        if get_channel(channel_id, author_id) is None:
            return False
        if timed_out(author_id, server_id):
            return False
        return bool(has_permission(author_id, server_id, "messages.send", channel_id))
    except Exception:
        return False


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

        # The inline message is a real persisted message posted through the
        # messaging layer, which does not apply the REST send route's
        # messages.send/timeout/channel-view gates. Enforce them here, fail
        # closed, before any message is persisted or broadcast.
        if not _can_emit_artifact_message(conversation_id, int(artifact.author_id)):
            logger.debug(
                "Skipping artifact message: author cannot post to the backing channel"
            )
            return

        content = (
            f"{config.get('artifacts', {}).get('inline_icon', '📎')} {artifact.title}"
        )

        send_rejected = False
        messaging_mod = api.get_messaging()
        if messaging_mod is not None and conversation_id is not None:
            try:
                messaging_mod.send_message(
                    user_id=int(artifact.author_id),
                    conversation_id=int(conversation_id),
                    content=content,
                )
            except Exception as e:  # best-effort: never break the artifact write
                send_rejected = True
                logger.debug(f"Failed to persist artifact message: {e}")
        if send_rejected:
            return

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
