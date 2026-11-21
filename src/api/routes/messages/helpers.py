import json
from typing import Optional, List, Dict, Any

from fastapi import HTTPException

from src.core.security.hardening import parse_id

import src.api as api
import utils.logger as logger
from src.api.schemas.messages import MessageResponse, AttachmentResponse, ReaderInfo
from src.api.schemas.polls import PollResponse, PollOptionResponse
from src.api.schemas.common import SnowflakeID
from src.core.notifications.parser import USER_MENTION_PATTERN
from src.core.polls import PollResultsVisibility


def require_snowflake(value, field: str = "id", message: str = "Invalid ID") -> int:
    """Parse an untrusted path/query id as a range-checked snowflake.

    Bare ``int()`` accepted negatives and values past the 64-bit snowflake
    ceiling, which then flowed into SQL lookups and offset arithmetic. Reuse
    the shared ``parse_id`` rule and map its rejection to the route's 400
    contract instead of leaking a 500.
    """
    try:
        return parse_id(value, field)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": message}},
        ) from exc


# BEFORE: ``_message_to_response`` called ``are_embeds_suppressed()`` once per
# message, so a single 100-message list page issued 100 SQL queries just to
# learn that none of them were suppressed.  After: one GROUP BY query over an
# IN (...) list (chunked to stay below the SQLite/Postgres parameter ceiling)
# resolves the whole page in a single round trip.  The per-id path remains as
# a fallback so a missing/renamed embeds table can never drop suppression.
_MAX_EMBED_SUPPRESSION_CHUNK = 500


def batch_embed_suppressed_ids(message_ids, embeds_mod=None) -> set:
    """Return the ids in ``message_ids`` whose embeds are ALL suppressed.

    A message with no embed rows is *not* suppressed; a message whose every
    embed row is flagged is.  This mirrors ``are_embeds_suppressed`` exactly
    but resolves an entire list in one query instead of N.
    """
    ids = [int(mid) for mid in message_ids if mid is not None]
    if not ids:
        return set()
    if embeds_mod is None:
        embeds_mod = api.get_embeds()

    db = _resolve_db(embeds_mod)

    suppressed: set = set()
    if db is not None and hasattr(db, "fetch_all"):
        try:
            for start in range(0, len(ids), _MAX_EMBED_SUPPRESSION_CHUNK):
                chunk = ids[start : start + _MAX_EMBED_SUPPRESSION_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows = db.fetch_all(
                    "SELECT message_id, COUNT(*) AS total, "
                    "COALESCE(SUM(suppressed), 0) AS suppressed_count "
                    f"FROM embed_message_embeds WHERE message_id IN ({placeholders}) "
                    "GROUP BY message_id",
                    tuple(chunk),
                )
                for row in rows:
                    total = int(row["total"] or 0)
                    suppressed_count = int(row["suppressed_count"] or 0)
                    if total and suppressed_count >= total:
                        suppressed.add(int(row["message_id"]))
            return suppressed
        except Exception as e:
            logger.debug(f"batch embed suppression lookup failed: {e}")

    # Fallback: preserve exact per-id semantics when the batch query is not
    # available (embeds module not wired, unexpected schema, etc.).
    for mid in ids:
        try:
            if embeds_mod and embeds_mod.are_embeds_suppressed(mid):
                suppressed.add(mid)
        except Exception as e:
            logger.debug(f"embed suppression lookup failed for {mid}: {e}")
    return suppressed


def _resolve_db(module_or_manager) -> Any:
    """Return a DB handle from either a core module or a manager instance.

    ``api.get_embeds()`` is the embeds *module* in production (it exposes
    ``_get_manager()``), but some deployments/tests wire the manager itself
    (which exposes ``_db``). The old code only handled the former: with a
    manager it raised on ``_get_manager()``, dropped the batched query and
    silently fell back to one ``are_embeds_suppressed()`` query per message.
    Accepting both wirings keeps the single-query path everywhere, with the
    per-id fallback retained only for genuine schema/module failures.
    """
    if module_or_manager is None:
        return None
    db = getattr(module_or_manager, "_db", None)
    if db is not None:
        return db
    try:
        manager = module_or_manager._get_manager()
    except Exception:
        return None
    return getattr(manager, "_db", None)


def _get_attr(obj, name, default=None):
    """Read an attribute from an object or a dict (storage rows are both)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _set_attr(obj, name, value) -> None:
    if isinstance(obj, dict):
        obj[name] = value
    else:
        setattr(obj, name, value)


def batch_attachment_file_ids(messages, media_mod=None) -> None:
    """Resolve missing attachment file ids in ONE batched media lookup.

    ``_message_to_response`` falls back to ``get_file_by_filename`` for any
    attachment whose URL points at the media API but whose metadata carries no
    ``file_id``. On a search/pins page that is one SQL query per attachment.
    Hydrating the metadata in bulk before serialization keeps the serializer on
    its zero-query path. Any lookup failure degrades to the original per-item
    behaviour, so a missing media module can never drop attachments.
    """
    if media_mod is None:
        media_mod = api.get_media()
    if not media_mod or not messages:
        return

    filenames = set()
    for msg in messages:
        for att in _get_attr(msg, "attachments") or ():
            metadata = _get_attr(att, "metadata")
            has_file_id = False
            if isinstance(metadata, dict):
                has_file_id = "file_id" in metadata
            elif isinstance(metadata, str):
                try:
                    has_file_id = "file_id" in json.loads(metadata)
                except (TypeError, ValueError):
                    pass
            url = _get_attr(att, "url")
            if not has_file_id and isinstance(url, str) and url:
                filenames.add(url.split("/")[-1])

    if not filenames:
        return
    try:
        file_map = media_mod.get_files_by_filenames(list(filenames)) or {}
    except Exception as e:
        logger.debug(f"batch attachment file id lookup failed: {e}")
        return
    if not file_map:
        return

    for msg in messages:
        for att in _get_attr(msg, "attachments") or ():
            url = _get_attr(att, "url")
            if not isinstance(url, str) or not url:
                continue
            fname = url.split("/")[-1]
            if fname not in file_map:
                continue
            metadata = _get_attr(att, "metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (TypeError, ValueError):
                    metadata = None
            if not isinstance(metadata, dict):
                metadata = {}
            _set_attr(att, "metadata", metadata)
            metadata["file_id"] = file_map[fname].id


def _poll_to_response(poll) -> PollResponse:
    options = [
        PollOptionResponse(
            id=opt.id,
            poll_id=opt.poll_id,
            text=opt.text,
            position=opt.position,
            vote_count=None,
        )
        for opt in poll.options
    ]
    return PollResponse(
        id=poll.id,
        message_id=poll.message_id,
        question=poll.question,
        created_by=poll.created_by,
        created_at=poll.created_at,
        ends_at=poll.ends_at,
        ended_at=poll.ended_at,
        allow_multiple_choice=poll.allow_multiple_choice,
        results_visibility=poll.results_visibility.value
        if isinstance(poll.results_visibility, PollResultsVisibility)
        else str(poll.results_visibility),
        options=options,
        total_votes=poll.total_votes,
        is_ended=poll.is_ended,
    )


def _message_to_response(
    msg,
    author_username: Optional[str] = None,
    author_avatar_url: Optional[str] = None,
    author_badges: Optional[List[str]] = None,
    channel_id: Optional[int] = None,
    reactions_data=None,
    read_by_data: Optional[List[Dict[str, Any]]] = None,
    media_mod=None,
    viewer_user_id: Optional[int] = None,
    poll_viewer_user_id: Optional[int] = None,
    suppress_embeds: Optional[bool] = None,
) -> MessageResponse:
    # Reuse the module-level accessor instead of allocating a fresh closure on
    # every call. A list/search/pins page serializes up to 100 messages, so this
    # used to create 100 throwaway function objects per request.
    get_attr = _get_attr

    msg_id = get_attr(msg, "id")
    author_id = get_attr(msg, "author_id")
    content = get_attr(msg, "content", "")
    created_at_value = get_attr(msg, "created_at")
    if created_at_value is None:
        created_at_value = get_attr(msg, "timestamp")
    if created_at_value is None:
        created_at_value = get_attr(msg, "created")
    created_at = 0
    if created_at_value is not None:
        try:
            if isinstance(created_at_value, (int, float)):
                created_at = int(created_at_value)
            elif (
                isinstance(created_at_value, str) and created_at_value.strip().isdigit()
            ):
                created_at = int(created_at_value.strip())
            else:
                created_at = int(created_at_value)
        except Exception as e:
            logger.warning(f"Invalid message timestamp {created_at_value}: {e}")
            created_at = 0

    attachments = []
    msg_attachments = get_attr(msg, "attachments")
    if msg_attachments:
        for att in msg_attachments:
            att_id = get_attr(att, "id")
            att_filename = get_attr(att, "filename", "attachment")
            att_url = get_attr(att, "url")
            att_content_type = get_attr(att, "content_type", "application/octet-stream")
            att_size = get_attr(att, "size", 0)
            att_hash = get_attr(att, "checksum") or get_attr(att, "hash")

            url = att_url if isinstance(att_url, str) else ""
            if media_mod and url and url.startswith("/api/v1/media/attachments/"):
                file_id = get_attr(att, "file_id")
                stored_name = ""
                if not file_id:
                    metadata = get_attr(att, "metadata")
                    if metadata:
                        if isinstance(metadata, dict):
                            file_id = metadata.get("file_id")
                        elif isinstance(metadata, str):
                            try:
                                meta = json.loads(metadata)
                                file_id = meta.get("file_id")
                            except (
                                TypeError,
                                ValueError,
                                AttributeError,
                            ) as meta_error:
                                logger.debug(
                                    f"attachment metadata unreadable: {meta_error}"
                                )

                if not file_id and att_url:
                    stored_name = att_url.split("/")[-1]
                    if stored_name:
                        media_file = media_mod.get_file_by_filename(stored_name)
                        if media_file:
                            file_id = media_file.id

                if file_id:
                    stored_tail = stored_name
                    if not stored_tail and att_url:
                        parts = att_url.split("/")
                        stored_tail = parts[-1] if parts else ""
                    if not stored_tail:
                        stored_tail = str(att_filename or "")
                    # SECURITY: signed URLs are bound to the viewing
                    # user (uid embedded in the HMAC and enforced at
                    # verification time), so a leaked URL is useless
                    # to anyone else. Only serialize-time viewers get
                    # a signature; anonymous/broadcast serialization
                    # keeps the raw URL, which stays protected by the
                    # standard auth + per-file access checks.
                    if viewer_user_id is not None:
                        try:
                            signed = media_mod.sign_url(
                                int(file_id),
                                user_id=viewer_user_id,
                                url=f"/api/v1/media/attachments/{stored_tail}",
                            )
                            url = signed.url
                        except Exception as e:
                            logger.warning(
                                f"Failed to sign attachment URL for file {file_id} "
                                f"(viewer {viewer_user_id}): {e}; "
                                "falling back to unsigned URL"
                            )

            attachments.append(
                AttachmentResponse(
                    id=SnowflakeID(att_id),
                    filename=att_filename,
                    content_type=att_content_type,
                    size=att_size,
                    url=url,
                    hash=att_hash,
                )
            )

    edited_at = None
    if get_attr(msg, "edited", False) or get_attr(msg, "edited_at"):
        edited_at = get_attr(msg, "edited_at") or get_attr(msg, "updated_at")

    effective_channel_id = (
        channel_id
        or get_attr(msg, "channel_id")
        or get_attr(msg, "conversation_id")
        or 0
    )

    read_by = []
    if read_by_data:
        read_by = [
            ReaderInfo(
                id=SnowflakeID(r["id"]),
                username=r["username"],
                avatar_url=r.get("avatar_url"),
            )
            for r in read_by_data
        ]

    read_count = (
        len(read_by) if read_by_data is not None else get_attr(msg, "read_count", 0)
    )

    metadata = get_attr(msg, "metadata")
    if metadata and isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = None

    poll_response = None
    poll_results = None
    poll_id = None
    if isinstance(metadata, dict):
        poll_id = metadata.get("poll_id")

    # ``poll_viewer_user_id`` lets broadcast emitters (e.g. AutoMod
    # tombstones) hydrate the poll for a context viewer without also
    # signing attachment URLs for that specific user.
    if poll_viewer_user_id is None:
        poll_viewer_user_id = viewer_user_id

    if poll_id and poll_viewer_user_id is not None:
        polls_module = api.get_polls()
        if polls_module:
            try:
                # get_results() already hydrates the complete poll (row +
                # options + voter stats) through get_poll(). Derive the plain
                # ``poll`` field from that same hydrated object instead of
                # issuing a second get_poll() round trip: on a list/search page
                # this used to double the poll-row/option/voter hydration and
                # authz gates for every poll-bearing message (an N+1 hotspot).
                results = polls_module.get_results(int(poll_id), poll_viewer_user_id)
                if results:
                    from src.api.routes.polls import _results_to_response

                    poll_response = _poll_to_response(results.poll)
                    if viewer_user_id is None:
                        # Fan-out broadcast (message delete / AutoMod tombstone):
                        # this single body reaches EVERY channel recipient, so it
                        # must not carry results resolved through one viewer's
                        # visibility window. ``poll_viewer_user_id`` is the
                        # message author there; for AFTER_VOTE / AFTER_END polls
                        # the author may be entitled to per-option counts, the
                        # voter headcount and their own individual votes -- none
                        # of which other recipients may see. Keep only the
                        # non-sensitive poll skeleton for UI continuity and fail
                        # closed on the viewer-specific pieces.
                        poll_response.total_votes = None
                    else:
                        poll_results = _results_to_response(results)
            except Exception as e:
                logger.debug(f"Failed to fetch poll/results for message {msg_id}: {e}")

    # SECURITY/UX parity: ``suppress_embeds`` flips the
    # ``embed_message_embeds.suppressed`` flag, but metadata-stored
    # embeds (``metadata["embeds"]``, what clients actually render)
    # previously ignored that state entirely, making suppression a dead
    # end-to-end control. If every attached embed is suppressed, omit
    # the embeds from the response and mark the message so clients can
    # render an appropriate indicator.
    embeds_out = get_attr(msg, "embeds", []) or []
    # ``suppress_embeds`` lets a list route pass the value resolved by
    # ``batch_embed_suppressed_ids`` so serialization adds zero queries. When
    # absent (single-message callers) keep the original one-off lookup.
    if suppress_embeds is None:
        try:
            embeds_module = api.get_embeds()
            if (
                embeds_module
                and msg_id
                and embeds_module.are_embeds_suppressed(int(msg_id))
            ):
                suppress_embeds = True
        except Exception as e:
            logger.debug(f"Failed to evaluate embed suppression for {msg_id}: {e}")
    if suppress_embeds:
        embeds_out = []
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata["embeds_suppressed"] = True
        # SECURITY: clients render the embed payload out of ``metadata["embeds"]``
        # (that is the surface the embed pipeline writes to), not just the
        # top-level ``embeds`` field. Dropping only ``embeds_out`` left the
        # suppressed link preview/chat card fully intact for every client and
        # made moderation suppression a no-op. Remove the metadata copy too so
        # a suppressed message cannot carry its embed content to viewers.
        metadata.pop("embeds", None)

    # Mention parity: clients (notifications.js) read ``mentions`` to
    # decide mention vs plain-message notifications. Parse user-mention
    # ids straight from the content so REST responses and broadcast
    # payloads carry the same array without extra service plumbing.
    mention_ids: List[str] = []
    try:
        raw_content = content if isinstance(content, str) else ""
        mention_ids = [m for m in USER_MENTION_PATTERN.findall(raw_content)]
    except Exception as e:
        logger.debug(f"Failed to parse mentions for message {msg_id}: {e}")

    # Resolve the fields the response reads more than once into locals so each
    # message attribute is looked up exactly once (status was read 3x and
    # reply_to_id 2x on every message of a list page).
    reply_to_id_value = get_attr(msg, "reply_to_id")
    status_value = get_attr(msg, "status")
    status_out = getattr(status_value, "value", status_value) if status_value else None

    return MessageResponse(
        id=SnowflakeID(msg_id),
        channel_id=SnowflakeID(effective_channel_id),
        author_id=SnowflakeID(author_id),
        content=content,
        created_at=created_at,
        edited_at=edited_at,
        reply_to_id=SnowflakeID(reply_to_id_value) if reply_to_id_value else None,
        attachments=attachments,
        embeds=embeds_out,
        pinned=get_attr(msg, "pinned", False),
        status=status_out,
        delivery_count=get_attr(msg, "delivery_count", 0),
        read_count=read_count,
        read=bool(get_attr(msg, "read", False)),
        read_by=read_by,
        author_username=author_username
        or get_attr(msg, "author_username")
        or f"User {author_id}",
        author_avatar_url=author_avatar_url or get_attr(msg, "author_avatar_url"),
        author_badges=author_badges or get_attr(msg, "author_badges") or [],
        mentions=mention_ids,
        reactions=reactions_data or [],
        metadata=metadata,
        deleted=bool(get_attr(msg, "deleted", False)),
        poll=poll_response,
        poll_results=poll_results,
    )
