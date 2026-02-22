from typing import Optional, List, Dict, Any

import src.api as api
import utils.logger as logger
from src.api.schemas.messages import MessageResponse, AttachmentResponse
from src.api.schemas.polls import PollResponse, PollOptionResponse
from src.api.schemas.common import SnowflakeID
from src.core.polls import PollResultsVisibility


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
) -> MessageResponse:
    def get_attr(obj, name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

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
                try:
                    file_id = get_attr(att, "file_id")
                    if not file_id:
                        metadata = get_attr(att, "metadata")
                        if metadata:
                            if isinstance(metadata, dict):
                                file_id = metadata.get("file_id")
                            elif isinstance(metadata, str):
                                import json

                                try:
                                    meta = json.loads(metadata)
                                    file_id = meta.get("file_id")
                                except Exception:
                                    pass

                    if not file_id and att_url:
                        stored_name = att_url.split("/")[-1]
                        if stored_name:
                            media_file = media_mod.get_file_by_filename(stored_name)
                            if media_file:
                                file_id = media_file.id

                    if file_id:
                        signed = media_mod.sign_url(int(file_id))
                        url = signed.url
                except Exception as e:
                    logger.debug(f"Failed to sign attachment URL: {e}")

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

    from src.api.schemas.messages import ReaderInfo

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
            import json

            metadata = json.loads(metadata)
        except Exception:
            metadata = None

    poll_response = None
    poll_results = None
    poll_id = None
    if isinstance(metadata, dict):
        poll_id = metadata.get("poll_id")

    if poll_id and viewer_user_id is not None:
        polls_module = api.get_polls()
        if polls_module:
            try:
                poll_obj = polls_module.get_poll(int(poll_id), viewer_user_id)
                if poll_obj:
                    poll_response = _poll_to_response(poll_obj)
                    # Include current results and user's voting status
                    results = polls_module.get_results(int(poll_id), viewer_user_id)
                    if results:
                        from src.api.routes.polls import _results_to_response

                        poll_results = _results_to_response(results)
            except Exception as e:
                logger.debug(f"Failed to fetch poll/results for message {msg_id}: {e}")

    return MessageResponse(
        id=SnowflakeID(msg_id),
        channel_id=SnowflakeID(effective_channel_id),
        author_id=SnowflakeID(author_id),
        content=content,
        created_at=created_at,
        edited_at=edited_at,
        reply_to_id=SnowflakeID(get_attr(msg, "reply_to_id"))
        if get_attr(msg, "reply_to_id")
        else None,
        attachments=attachments,
        embeds=get_attr(msg, "embeds", []) or [],
        pinned=get_attr(msg, "pinned", False),
        status=getattr(get_attr(msg, "status"), "value", get_attr(msg, "status"))
        if get_attr(msg, "status")
        else None,
        delivery_count=get_attr(msg, "delivery_count", 0),
        read_count=read_count,
        read=bool(get_attr(msg, "read", False)),
        read_by=read_by,
        author_username=author_username
        or get_attr(msg, "author_username")
        or f"User {author_id}",
        author_avatar_url=author_avatar_url or get_attr(msg, "author_avatar_url"),
        author_badges=author_badges or get_attr(msg, "author_badges") or [],
        reactions=reactions_data or [],
        metadata=metadata,
        poll=poll_response,
        poll_results=poll_results,
    )
