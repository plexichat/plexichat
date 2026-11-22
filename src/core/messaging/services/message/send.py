"""
Send message operations mixin for the MessageService.
"""

from typing import Any, Dict, List, Optional

from urllib.parse import urlparse

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cache_set
from src.core.embeds.validator import sanitize_message_embeds
from src.utils.encryption import (
    encrypt_message,
    blind_index,
    encrypt_data,
)

from ...models import (
    Message,
    MessageType,
    Attachment,
    MessageStatusType,
    ConversationType,
)
from ...exceptions import (
    ConversationAccessDeniedError,
    ConversationNotFoundError,
    MessageNotFoundError,
    InvalidContentError,
    ContentTooLongError,
    AttachmentLimitError,
)
from ..base import BaseService, _get_relationships_module, get_encrypt_messages_default
from .protocol import MessageServiceProtocol
from .search_helpers import search_index_message


class SendMixin(BaseService, MessageServiceProtocol):
    """Mixin providing send-related message operations."""

    _ratchet_manager: Any = None

    def send_message(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        content: str,
        message_type: MessageType = MessageType.TEXT,
        reply_to_id: Optional[SnowflakeID] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        embeds: Optional[List[Dict[str, Any]]] = None,
        webhook_id: Optional[SnowflakeID] = None,
    ) -> Message:
        """Send a message to a conversation."""
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError(
                "Not a participant in this conversation"
            )

        self._enforce_dm_block_rules(conversation_id, user_id)
        self._enforce_dm_recipient_privacy(conversation_id, user_id)

        user_settings = self._user_settings_svc.get_message_settings(user_id)
        max_length = user_settings.max_message_length or self._get_config(
            "max_message_length", 4000
        )

        user_filter = self._content_filter_svc.get_filter_settings(user_id)
        content_result = self._content_filter_svc.validate_content(
            content, user_filter, max_length
        )

        if not content_result.valid:
            if any(
                "exceeds maximum length" in issue for issue in content_result.issues
            ):
                raise ContentTooLongError(
                    f"Message exceeds maximum length of {max_length}",
                    max_length,
                    len(content),
                )
            raise InvalidContentError("Invalid message content", content_result.issues)

        if reply_to_id:
            if not self._repo.exists_in_conversation(reply_to_id, conversation_id):
                raise MessageNotFoundError(
                    "Reply target message not found in this conversation"
                )
            reply_row = self._repo.get_by_id(reply_to_id)
            if reply_row and reply_row.get("deleted"):
                raise MessageNotFoundError("Reply target message has been deleted")

        if attachments:
            max_attachments = (
                user_settings.max_attachments_per_message
                or self._get_config("max_attachments_per_message", 10)
            )
            if len(attachments) > max_attachments:
                raise AttachmentLimitError(
                    f"Cannot attach more than {max_attachments} files",
                    max_attachments,
                    len(attachments),
                )

        now = self._get_timestamp()
        msg_id = self._generate_id()

        final_content = content_result.sanitized_content
        content_idx = blind_index(final_content, "message_content")

        ratchet_interval_id = None
        from src.utils.encryption.channel_ratchet import (
            ratchet_encryption_licensed,
        )

        if self._get_config("encrypt_messages", get_encrypt_messages_default()):
            if self._ratchet_manager is not None and ratchet_encryption_licensed():
                ratchet_result = self._ratchet_manager.encrypt(
                    conversation_id=conversation_id,
                    message_id=msg_id,
                    plaintext=final_content.encode("utf-8"),
                    now=now,
                )
                final_content = ratchet_result.envelope
                ratchet_interval_id = ratchet_result.interval_id
            else:
                final_content = encrypt_message(final_content, msg_id)

        metadata: Dict[str, Any] = {}
        if content_result.has_spoilers:
            metadata["has_spoilers"] = True
        if content_result.has_nsfw:
            metadata["has_nsfw"] = True
        if content_result.filtered_words:
            metadata["filtered"] = True
        if embeds:
            metadata["embeds"] = sanitize_message_embeds(embeds)

        try:
            self._repo.begin_transaction()
            self._repo.create(
                msg_id,
                conversation_id,
                user_id,
                final_content,
                message_type,
                now,
                reply_to_id=reply_to_id,
                content_index=content_idx,
                metadata=metadata if metadata else None,
                webhook_id=webhook_id,
                ratchet_interval_id=ratchet_interval_id,
                auto_commit=False,
            )

            self._conversation_repo.update(
                conv_id=conversation_id,
                updated_at=now,
                last_message_id=msg_id,
                last_message_at=now,
                auto_commit=False,
            )

            # Store per-word blind indexes so substring search keeps working
            # for encrypted messages (content holds the ENC envelope; the
            # whole-content content_index only supports exact equality).
            token_indexes = self._repo.content_token_indexes(
                content_result.sanitized_content
            )
            self._repo.set_content_tokens(msg_id, token_indexes, auto_commit=False)

            status_id = self._generate_id()
            self._status_repo.create(
                status_id=status_id,
                message_id=msg_id,
                user_id=user_id,
                status=MessageStatusType.SENT,
                timestamp=now,
                auto_commit=False,
            )

            attachment_list: List[Attachment] = []
            if attachments:
                bulk_data = []
                for att_data in attachments:
                    att_id = self._generate_id()
                    normalized_url = self._normalize_url(att_data.get("url", ""))
                    # Defense in depth: attachment URLs are attacker-controlled
                    # and, unlike embed URLs, were never validated for scheme or
                    # SSRF. A ``javascript:``/``data:`` URL or an internal host
                    # stored here is later echoed verbatim to every client via
                    # _message_to_response, so it must be constrained to the
                    # shapes the media pipeline actually produces. Relative
                    # ``/api/v1/media/...`` paths are the legitimate form;
                    # anything absolute must be http(s) and pass the same
                    # private/loopback/link-local SSRF gate embeds use.
                    self._validate_attachment_url(normalized_url)
                    encrypted_url = None
                    stored_url = normalized_url
                    if self._get_config("encrypt_attachments"):
                        encrypted_url = encrypt_data(normalized_url)
                        stored_url = "[encrypted]"
                    bulk_data.append(
                        {
                            "id": att_id,
                            "message_id": msg_id,
                            "filename": att_data.get("filename", "file"),
                            "content_type": att_data.get(
                                "content_type", "application/octet-stream"
                            ),
                            "size": att_data.get("size", 0),
                            "url": stored_url,
                            "url_encrypted": encrypted_url,
                            "created_at": now,
                            "metadata": att_data.get("metadata"),
                            "checksum": att_data.get("hash")
                            or att_data.get("checksum"),
                        }
                    )
                    attachment_list.append(
                        Attachment(
                            id=att_id,
                            message_id=msg_id,
                            filename=att_data.get("filename", "file"),
                            content_type=att_data.get(
                                "content_type", "application/octet-stream"
                            ),
                            size=att_data.get("size", 0),
                            url=normalized_url,
                            created_at=now,
                            checksum=att_data.get("hash") or att_data.get("checksum"),
                        )
                    )
                self._attachment_repo.create_bulk(bulk_data, auto_commit=False)

            self._repo.commit()
        except Exception:
            self._repo.rollback()
            raise

        logger.debug(f"Message {msg_id} sent to conversation {conversation_id}")

        if (
            self._ratchet_manager is not None
            and ratchet_interval_id is not None
            and ratchet_encryption_licensed()
        ):
            try:
                new_interval = self._ratchet_manager.rotate_if_due(
                    conversation_id=conversation_id,
                    last_message_id=msg_id,
                    now_ms=now,
                )
                if new_interval is not None:
                    try:
                        from src.utils.encryption.channel_ratchet import (
                            notify_ratchet_update,
                        )

                        notify_ratchet_update(
                            conversation_id=int(conversation_id),
                            update_data={
                                "reason": "rotation",
                                "new_interval_id": int(new_interval.interval_id),
                                "at_message_id": int(new_interval.start_message_id),
                                "at": int(now),
                            },
                        )
                    except Exception as e:
                        logger.debug(
                            f"Ratchet rotation broadcast scheduling failed: {e}"
                        )
            except Exception as e:
                logger.debug(f"Ratchet rotation check failed: {e}")

        msg = Message(
            id=msg_id,
            conversation_id=conversation_id,
            author_id=user_id,
            content=content_result.sanitized_content,
            message_type=message_type,
            created_at=now,
            updated_at=now,
            reply_to_id=reply_to_id,
            edited=False,
            deleted=False,
            pinned=False,
            metadata=metadata if metadata else None,
            embeds=metadata.get("embeds") or [],
            attachments=attachment_list,
        )

        try:
            search_index_message(
                msg_id,
                content_result.sanitized_content,
                {
                    "author_id": user_id,
                    "conversation_id": conversation_id,
                    "created_at": now,
                    "has_attachments": bool(attachments),
                    "has_embeds": bool(embeds),
                    "source_updated_at": now,
                },
            )
        except Exception as e:
            logger.debug(f"Failed to index message for search: {e}")

        self._dispatch_message_notifications(
            msg_id=msg_id,
            conversation_id=conversation_id,
            author_id=user_id,
            content=content_result.sanitized_content,
        )

        cache_set(f"msg:obj:{msg_id}", msg, ttl=3600)

        try:
            from src.core.database import get_valkey_client as get_client

            client = get_client()
            if client:
                list_key = f"msg:recent:{conversation_id}"
                client.lpush(list_key, msg_id)
                client.ltrim(list_key, 0, 99)
                client.expire(list_key, 3600)
        except Exception as e:
            logger.debug(f"Failed to update recent messages cache: {e}")

        # A new message raises the unread badge for every participant except
        # the author. The bounded service-level aggregate is keyed by user and
        # the author list is not resolved here, so drop the whole namespace;
        # the next unread read recomputes from the just-committed rows.
        try:
            from ..unread_cache import invalidate_all as _invalidate_unread_cache

            _invalidate_unread_cache()
        except Exception as e:
            logger.debug(f"Failed to invalidate unread aggregate cache: {e}")

        return msg

    def _dispatch_message_notifications(
        self,
        msg_id: SnowflakeID,
        conversation_id: SnowflakeID,
        author_id: SnowflakeID,
        content: str,
    ) -> None:
        """Best-effort server-side mention notifications for a sent message.

        NEVER raises and NEVER blocks delivery: the whole fan-out (channel
        resolution, per-user permission checks, inserts) runs on the
        notifications module's bounded background consumer instead of the
        caller's thread, so a 10k-recipient mention cannot stall sends.
        If the notifications module is not initialized (embedded
        deployments, some test setups) or any step fails, we log at debug
        and return. Recipient collection inside the notifications module
        enforces per-user channel view permissions so previews never leak
        to users who cannot see the channel; device push delivery is
        queued onto the same consumer by the creator itself.
        """
        try:
            from src.core import notifications as notifications_module

            if not (
                getattr(notifications_module, "_setup_complete", False)
                and getattr(notifications_module, "_manager", None) is not None
            ):
                return

            from src.core.notifications.background import (
                get_notification_dispatcher,
                is_sync_notification_dispatch,
            )

            def _fanout_job():
                try:
                    # Resolve the server channel backing this conversation
                    # (DMs have none — server_id/channel_id stay unset).
                    server_id = None
                    channel_id = None
                    try:
                        channel_row = self._db.fetch_one(
                            """SELECT id, server_id FROM srv_channels
                               WHERE conversation_id = ? AND deleted = 0 LIMIT 1""",
                            (conversation_id,),
                        )
                        if channel_row:
                            channel_id = channel_row["id"]
                            server_id = channel_row["server_id"]
                    except Exception as e:
                        logger.debug(f"Channel lookup for notifications failed: {e}")

                    created = notifications_module.create_notifications_for_message(
                        message_id=msg_id,
                        author_id=author_id,
                        conversation_id=conversation_id,
                        content=content,
                        server_id=server_id,
                        channel_id=channel_id,
                    )
                    if created:
                        logger.debug(
                            f"Created {len(created)} notification(s) for "
                            f"message {msg_id}"
                        )
                except Exception as e:
                    logger.debug(
                        f"Notification creation skipped for message {msg_id}: {e}"
                    )

            if is_sync_notification_dispatch():
                _fanout_job()
                return

            # Key by message id so a burst of duplicate scheduling for the
            # same message cannot enqueue the same fan-out more than once.
            if not get_notification_dispatcher().submit(
                _fanout_job, key=("notify-fanout", str(msg_id))
            ):
                logger.debug(
                    f"Notification dispatch queue full or duplicate; "
                    f"skipped for message {msg_id}"
                )
        except Exception as e:
            logger.debug(f"Notification scheduling failed for message {msg_id}: {e}")

    def _enforce_dm_recipient_privacy(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> None:
        """Re-check every DM recipient's ``allow_dms_from`` setting at send time.

        ``create_dm`` enforces ``allow_dms_from`` (friends/none) only when the
        conversation is *opened*.  The send path used to check just block
        state, so a party to an already-open DM could keep writing into it
        after the recipient switched to ``none``, or without ever being a
        friend when the recipient requires ``friends``.  Every user-facing
        send path (REST, forward, scheduled dispatch, reaction mentions)
        funnels through ``send_message``, so gating here closes all of them.

        Scoped to single DMs: group-DM membership is gated at *insertion* by
        ``ConversationService.enforce_group_dm_privacy`` and server channels
        are governed by server membership, so re-checking per send there would
        be both redundant and wrong.

        Fails closed: an unreadable conversation/metadata, a failed settings
        lookup, or an unavailable relationships module with a friends-only
        recipient all deny the send rather than silently allowing it.
        """
        conv_row = self._conversation_repo.get_by_id(conversation_id)
        if not conv_row or conv_row.get("deleted"):
            return

        conv_type = conv_row.get("conversation_type")
        if isinstance(conv_type, str):
            conv_type = conv_type.lower()
        else:
            conv_type = getattr(conv_type, "value", conv_type)
        if conv_type != ConversationType.DM.value:
            return

        try:
            recipient_ids = [
                pid
                for pid in self._participant_svc.get_participant_ids(conversation_id)
                if int(pid) != int(user_id)
            ]
        except Exception as e:
            logger.warning(
                f"DM recipient resolution failed for conversation "
                f"{conversation_id} (failing closed): {e}"
            )
            raise ConversationAccessDeniedError(
                "Cannot send messages in this conversation"
            ) from e

        relationships = _get_relationships_module()
        for recipient_id in recipient_ids:
            try:
                settings = self._user_settings_svc.get_message_settings(recipient_id)
            except Exception as e:
                logger.warning(
                    f"DM recipient settings lookup failed for user "
                    f"{recipient_id} (failing closed): {e}"
                )
                raise ConversationAccessDeniedError(
                    "Cannot send messages in this conversation"
                ) from e

            if settings.allow_dms_from == "none":
                raise ConversationAccessDeniedError(
                    "Cannot send messages in this conversation"
                )
            if settings.allow_dms_from == "friends":
                if relationships is None:
                    # No relationship source: cannot prove friendship, deny.
                    raise ConversationAccessDeniedError(
                        "Cannot send messages in this conversation"
                    )
                try:
                    friend_ids = relationships.get_friend_ids(recipient_id) or []
                    if int(user_id) not in {int(fid) for fid in friend_ids}:
                        raise ConversationAccessDeniedError(
                            "Cannot send messages in this conversation"
                        )
                except ConversationAccessDeniedError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"DM friendship lookup failed for user "
                        f"{recipient_id} (failing closed): {e}"
                    )
                    raise ConversationAccessDeniedError(
                        "Cannot send messages in this conversation"
                    ) from e

    def _enforce_dm_block_rules(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> None:
        """Reject sends into a DM/group DM whose participants block each other.

        Keeps conversations created before a block (DMs and group DMs alike)
        from remaining writable by either blocked party. Group DMs are
        ``GROUP`` rows; peers close their *insertion* against blocked pairs,
        but a pair already co-resident before the block could otherwise keep
        delivering messages into the same group DM because this gate used to
        return early for every non-``DM`` type. Server-channel conversations
        are also ``GROUP`` rows, but they are governed by server membership
        and channel permissions rather than DM privacy, so they stay exempt
        (mirrors ``ConversationService.enforce_conversation_dm_privacy``).
        Fails closed: when the optional relationships module is unavailable
        the authoritative ``rel_blocked`` table is consulted directly, and a
        failed lookup denies the send.
        """
        conv_row = self._conversation_repo.get_by_id(conversation_id)
        if not conv_row or conv_row.get("deleted"):
            return

        conv_type = conv_row.get("conversation_type")
        if isinstance(conv_type, str):
            conv_type = conv_type.lower()
        else:
            conv_type = getattr(conv_type, "value", conv_type)

        if conv_type == ConversationType.DM.value:
            pass
        elif conv_type == ConversationType.GROUP.value:
            # A GROUP row carrying a server_id is a server channel, not a
            # group DM: blocks are not a send boundary there. Anything else
            # GROUP-shaped is treated as a group DM, including rows with
            # unreadable metadata, so a malformed row cannot downgrade the
            # check.
            metadata = self._conversation_repo._json_loads(conv_row.get("metadata"))
            if isinstance(metadata, dict) and metadata.get("server_id"):
                return
        else:
            return

        relationships = _get_relationships_module()

        other_ids = [
            pid
            for pid in self._participant_svc.get_participant_ids(conversation_id)
            if pid != user_id
        ]

        if relationships is not None:
            # One cached two-way block set per sender replaces an authoritative
            # rel_blocked probe per recipient on every send. For a DM that is
            # one query avoided per message; for a group it also stops the
            # check scaling linearly with the participant count. get_all_blocked_ids
            # is the UNION of "users I blocked" and "users who blocked me"
            # already used by DM event fan-out, so set membership is exactly
            # equivalent to the old is_blocked_by_either OR probe: both block
            # directions stay authoritative. The set is cached per sender under
            # user_blocked_all:<id> and invalidated for both parties on
            # block/unblock, so authz is unchanged and the shared Valkey tier
            # keeps every node coherent. The module-down fallback below still
            # consults rel_blocked directly and fails closed.
            blocked_ids = {
                int(bid) for bid in (relationships.get_all_blocked_ids(user_id) or [])
            }
            for other_id in other_ids:
                if int(other_id) in blocked_ids:
                    raise ConversationAccessDeniedError(
                        "Cannot send messages in this conversation"
                    )
            return

        # The optional relationships module can be unavailable (not
        # initialised in embedded deployments, or a partial outage) while the
        # block rows are still authoritative in the database. Returning here
        # used to FAIL OPEN: either party could keep writing into a DM after a
        # block, bypassing the same rule create_dm/typing now enforce even
        # when the module is down. Probe rel_blocked directly and deny if
        # either direction is present, or if the probe itself fails (with no
        # relationship source we cannot prove the pair is unblocked).
        try:
            for other_id in other_ids:
                blocked_row = self._db.fetch_one(
                    "SELECT 1 AS ok FROM rel_blocked WHERE "
                    "(blocker_id = ? AND blocked_id = ?) OR "
                    "(blocker_id = ? AND blocked_id = ?)",
                    (int(user_id), int(other_id), int(other_id), int(user_id)),
                )
                if blocked_row is not None:
                    raise ConversationAccessDeniedError(
                        "Cannot send messages in this conversation"
                    )
        except ConversationAccessDeniedError:
            raise
        except Exception as e:
            logger.warning(
                f"DM block fallback lookup failed for conversation "
                f"{conversation_id} (failing closed): {e}"
            )
            raise ConversationAccessDeniedError(
                "Cannot send messages in this conversation"
            )

    # Attachment URL scheme allowlist (L4): only absolute http(s) URLs and
    # site-relative media paths are accepted. Everything else — javascript:,
    # data:, vbscript:, file:, protocol-relative "//host", etc. — is
    # rejected at send time so dangerous schemes can never reach storage.
    _ALLOWED_ATTACHMENT_SCHEMES = frozenset({"http", "https"})

    def _normalize_url(self, url: str) -> str:
        if not url:
            return url

        candidate = url.strip()
        if not candidate:
            return candidate

        lowered = candidate.lower()

        # Site-relative media path (e.g. /api/v1/media/attachments/...).
        # Must start with a single "/" and must not be protocol-relative.
        if candidate.startswith("/"):
            if lowered.startswith("//") or candidate.startswith("/\\"):
                raise InvalidContentError(
                    "Attachment URL scheme is not allowed",
                    [f"disallowed url: {candidate[:120]}"],
                )
            return candidate

        parsed = urlparse(candidate)
        if parsed.scheme in self._ALLOWED_ATTACHMENT_SCHEMES and parsed.netloc:
            return candidate

        raise InvalidContentError(
            "Attachment URL scheme is not allowed",
            [f"disallowed url scheme: {candidate[:120]}"],
        )

    def _validate_attachment_url(self, url: str) -> None:
        """Defence-in-depth SSRF gate for attacker-controlled attachment URLs.

        ``_normalize_url`` already enforces the scheme allow-list, but an
        absolute ``http(s)://`` URL whose host resolves to a private, loopback,
        link-local or otherwise reserved address would still pass and later be
        echoed verbatim to every client that renders the message. Absolute URLs
        are therefore run through the same ``URLValidator`` the embed path uses;
        site-relative media paths (``/api/v1/media/...``) are trusted because
        they are minted server-side by the upload pipeline.

        Fails closed on infrastructure errors so a missing/broken validator can
        never silently downgrade to "no SSRF protection".
        """
        if not url:
            return
        candidate = url.strip()
        if not candidate:
            return
        if candidate.startswith("/"):
            # Server-relative media path: never a network target.
            return
        try:
            from src.utils.security import URLValidator

            URLValidator().validate_url_for_request(candidate)
        except Exception as exc:
            logger.warning(f"Rejected attachment URL failing SSRF check: {exc}")
            raise InvalidContentError(
                "Attachment URL failed SSRF validation",
                [f"unsafe url: {candidate[:120]}"],
            )

    def send_system_message(
        self,
        conversation_id: SnowflakeID,
        content: str,
        event_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        """Send a system message.

        Encryption parity (L2): when the conversation encryption default is
        enabled the content is routed through the SAME ``encrypt_message``
        path user messages take when no channel ratchet applies. The
        per-message channel-ratchet envelope is NOT applied here — system
        messages are not part of any key interval (author id 0) — so on an
        encrypted deployment nothing is stored in plaintext, but the
        ciphertext is the standard ``encrypt_message`` envelope carrying
        its ``system`` metadata flag and ``event_type``, not ratchet
        framing. The deterministic blind index of the plaintext is stored
        for search parity.

        NOTE on author id 0: system messages keep ``author_id = 0`` which
        has NO matching auth_users row. SQLite does not enforce the FK so
        this works, but PostgreSQL deployments must either disable FK
        enforcement for msg_messages.author_id or provision a reserved
        system user with id 0 before migrating.
        """
        conv_row = self._conversation_repo.get_by_id(conversation_id)
        if not conv_row or conv_row.get("deleted"):
            raise ConversationNotFoundError("Conversation not found")

        now = self._get_timestamp()
        msg_id = self._generate_id()

        full_metadata: Dict[str, Any] = {
            "event_type": event_type,
            "system": True,
        }
        if metadata:
            full_metadata.update(metadata)

        final_content = content
        content_idx = blind_index(content, "message_content")
        if self._get_config("encrypt_messages", get_encrypt_messages_default()):
            final_content = encrypt_message(final_content, msg_id)

        self._repo.create(
            msg_id,
            conversation_id,
            0,
            final_content,
            MessageType.SYSTEM,
            now,
            content_index=content_idx,
            metadata=full_metadata,
        )

        row = self._repo.get_by_id(msg_id)
        if row is None:
            raise MessageNotFoundError("Failed to create system message")

        # System messages are also part of the unread aggregate. Same eager
        # namespace drop as the regular send path.
        try:
            from ..unread_cache import invalidate_all as _invalidate_unread_cache

            _invalidate_unread_cache()
        except Exception as e:
            logger.debug(f"Failed to invalidate unread aggregate cache: {e}")

        return self._repo.row_to_model(row)
