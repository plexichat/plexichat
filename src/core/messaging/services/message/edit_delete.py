"""
Edit and delete message operations mixin for the MessageService.
"""

from typing import Any, Dict, List, Optional

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import cache_set, cache_delete
from src.utils.encryption import (
    encrypt_message,
    blind_index,
)

from ...models import Message, ParticipantRole
from ...exceptions import (
    MessageNotFoundError,
    MessageAccessDeniedError,
    InvalidContentError,
    MessageConflictError,
    MessageEditWindowExpiredError,
)
from ..base import BaseService, get_encrypt_messages_default
from .protocol import MessageServiceProtocol
from .search_helpers import search_index_message, search_remove_from_index

BULK_DELETE_MAX_IDS = 100


def _get_servers_module():
    """Return the servers module without importing the whole API app.

    Mirrors the lazy-resolution strategy in services/message_status.py:
    prefer the already-configured ``src.api`` provider, fall back to the
    light ``src.core.servers`` import.
    """
    import sys

    api_mod = sys.modules.get("src.api")
    if api_mod is not None:
        get_servers = getattr(api_mod, "get_servers", None)
        if get_servers is not None:
            try:
                servers = get_servers()
                if servers:
                    return servers
            except Exception:
                pass
    try:
        from src.core import servers

        return servers
    except ImportError:
        return None


def _resolve_channel_conversation_scope(channel_id) -> set:
    """Resolve a URL channel id to the set of conversation ids it may map to.

    A channel id in this codebase is either a conversation id itself
    (DM/group conversations) or a server channel id whose messages live in
    the linked ``conversation_id``. Returns both candidates so callers can
    scope operations to the requested channel.
    """
    scope = {int(channel_id)}
    servers = _get_servers_module()
    if servers is None:
        return scope
    getter = getattr(servers, "get_channel_conversation_id", None)
    if getter is None:
        return scope
    try:
        linked = getter(int(channel_id))
        if linked:
            scope.add(int(linked))
    except Exception as e:
        logger.debug(f"Failed to resolve conversation for channel {channel_id}: {e}")
    return scope


def _is_server_message_moderator(user_id, conversation_id) -> bool:
    """True when user holds server ``messages.manage`` on the channel that
    backs ``conversation_id`` (server-channel moderator path).
    """
    servers = _get_servers_module()
    if servers is None:
        return False
    lookup = getattr(servers, "get_channel_by_conversation", None)
    if lookup is None:
        return False
    try:
        info = lookup(int(conversation_id))
    except Exception as e:
        logger.debug(
            f"Failed to resolve server channel for conversation {conversation_id}: {e}"
        )
        return False
    if not info:
        return False
    has_permission = getattr(servers, "has_permission", None)
    if has_permission is None:
        return False
    try:
        return bool(
            has_permission(
                int(user_id),
                int(info["server_id"]),
                "messages.manage",
                int(info["id"]),
            )
        )
    except Exception as e:
        logger.debug(f"Server moderator permission check failed: {e}")
        return False


class EditDeleteMixin(BaseService, MessageServiceProtocol):
    @staticmethod
    def _cleanup_thread_links(message_id) -> None:
        """Best-effort unlink of a deleted message from any threads.

        When the threads module is wired, every ``thread_messages`` link
        referencing this message is dropped, affected thread counters are
        re-synced and starter linkage is cleared. Failures never block
        the delete itself.
        """
        try:
            from src.core import threads as threads_mod

            threads_mod.unlink_deleted_message(int(message_id))
        except Exception as e:
            logger.debug(f"Thread link cleanup skipped for {message_id}: {e}")

    """Mixin providing edit and delete operations."""

    _ratchet_manager: Any = None

    def _can_edit_outside_window(self, user_id, conversation_id) -> bool:
        """Moderator exemption from the edit-window restriction.

        Conversation OWNER/ADMIN participants and server users holding
        ``messages.manage`` on the backing channel may edit regardless of
        the configured ``messaging.edit_window_seconds`` window.
        """
        try:
            participant = self._participant_svc.get_participant(
                conversation_id, user_id
            )
            if participant and participant.role in [
                ParticipantRole.OWNER,
                ParticipantRole.ADMIN,
            ]:
                return True
        except Exception as e:
            logger.debug(f"Edit-window participant check failed: {e}")
        return _is_server_message_moderator(user_id, conversation_id)

    def edit_message(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        content: str,
        expected_edited_at: Optional[int] = None,
    ) -> Message:
        """Edit a message (own messages only).

        ``expected_edited_at`` is an optional optimistic-concurrency token:
        when provided it must match the message's current revision
        (``edited_at``, falling back to ``updated_at`` for never-edited
        messages) or a :class:`MessageConflictError` is raised. Clients
        that ignore the field keep full backward compatibility (the token
        is simply not enforced).
        """
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row or msg_row["deleted"]:
            raise MessageNotFoundError("Message not found")

        if not self._participant_svc.is_participant(
            msg_row["conversation_id"], user_id
        ):
            raise MessageNotFoundError("Message not found")

        if msg_row["author_id"] != user_id:
            raise MessageAccessDeniedError("Can only edit own messages")

        # Optimistic locking (M3): reject stale writes when the client
        # supplied a revision token that no longer matches.
        if expected_edited_at is not None:
            current_revision = msg_row.get("edited_at") or msg_row.get("updated_at")
            if current_revision is None or int(expected_edited_at) != int(
                current_revision
            ):
                raise MessageConflictError(
                    "Message was modified by another request; refresh and retry",
                    expected=expected_edited_at,
                    actual=current_revision,
                )

        # Configurable edit window (parity #1): 0 (the default) means no
        # limit; a positive value restricts edits to that many seconds
        # after creation, except for moderators/admins.
        edit_window_seconds = int(self._get_config("edit_window_seconds", 0) or 0)
        if edit_window_seconds > 0:
            now_check = self._get_timestamp()
            created_at = int(msg_row.get("created_at") or 0)
            if (
                created_at > 0
                and now_check - created_at > edit_window_seconds * 1000
                and not self._can_edit_outside_window(
                    user_id, msg_row["conversation_id"]
                )
            ):
                raise MessageEditWindowExpiredError(
                    "Edit window has expired for this message"
                )

        user_settings = self._user_settings_svc.get_message_settings(user_id)
        max_length = user_settings.max_message_length or self._get_config(
            "max_message_length", 4000
        )
        user_filter = self._content_filter_svc.get_filter_settings(user_id)

        content_result = self._content_filter_svc.validate_content(
            content, user_filter, max_length
        )
        if not content_result.valid:
            raise InvalidContentError("Invalid message content", content_result.issues)

        now = self._get_timestamp()
        final_content = content_result.sanitized_content
        content_idx = blind_index(final_content, "message_content")

        # No-op detection MUST happen on plaintext: AES-GCM uses a random
        # nonce per encryption, so resubmitting identical text yields a
        # different ciphertext and would falsely register as an edit (raising
        # ``edited`` and rotating the cipher). The deterministic blind index
        # of the sanitized plaintext is compared instead — it is stored for
        # every message by both the send path (send.py) and this edit path,
        # so identical content is detected regardless of encryption mode.
        existing_index = msg_row.get("content_index") if msg_row else None
        if existing_index is not None and str(existing_index) == content_idx:
            msg = self.get_message(user_id, message_id)
            if msg is None:
                raise MessageNotFoundError("Failed to retrieve message")
            return msg

        if self._get_config("encrypt_messages", get_encrypt_messages_default()):
            from src.utils.encryption.channel_ratchet import (
                ratchet_encryption_licensed,
            )

            if (
                self._ratchet_manager is not None
                and ratchet_encryption_licensed()
                and msg_row.get("conversation_id")
            ):
                try:
                    conversation_id_int = int(msg_row["conversation_id"])
                    previous_interval = None
                    try:
                        previous_interval = self._ratchet_manager.get_active_interval(
                            conversation_id_int
                        )
                    except Exception:
                        previous_interval = None

                    ratchet_result = self._ratchet_manager.encrypt(
                        conversation_id=conversation_id_int,
                        message_id=message_id,
                        plaintext=final_content.encode("utf-8"),
                        now=now,
                    )
                    final_content = ratchet_result.envelope

                    # Editing an out-of-interval message silently opens a new
                    # interval (closing the previously active one). Clients
                    # caching the active key MUST be told, exactly like the
                    # send/delete paths, or their next decryption fails.
                    if previous_interval is not None and int(
                        ratchet_result.interval_id
                    ) != int(previous_interval.interval_id):
                        try:
                            from src.utils.encryption.channel_ratchet import (
                                notify_ratchet_update,
                            )

                            notify_ratchet_update(
                                conversation_id=conversation_id_int,
                                update_data={
                                    "reason": "rotation",
                                    "new_interval_id": int(ratchet_result.interval_id),
                                    "at_message_id": int(message_id),
                                    "edited_message_id": int(message_id),
                                    "at": int(now),
                                },
                            )
                        except Exception as e:
                            logger.debug(
                                f"Ratchet rotation broadcast scheduling failed: {e}"
                            )
                except Exception:
                    final_content = encrypt_message(final_content, message_id)
            else:
                final_content = encrypt_message(final_content, message_id)

        # Record the PRIOR content in the edit history before it is
        # overwritten (parity #2). The history service encrypts the prior
        # content at rest when encryption is enabled; failures are
        # best-effort and never block the edit itself.
        try:
            from ..edit_history import EditHistoryService

            history_svc = EditHistoryService(
                self._db,
                self._participant_svc,
                encryption_enabled=get_encrypt_messages_default(),
            )
            prior_row = self._repo.get_by_id(message_id)
            if prior_row is not None:
                prior_content = self._repo.row_to_model(prior_row).content
                history_svc.record_edit(user_id, message_id, prior_content)
        except Exception as exc:
            logger.debug(f"Failed to record edit history for {message_id}: {exc}")

        self._repo.update_content(
            message_id, final_content, now, content_index=content_idx
        )
        # Rebuild the per-word search blind indexes for the new plaintext so
        # substring search keeps working after edits (see send path).
        try:
            token_indexes = self._repo.content_token_indexes(
                content_result.sanitized_content
            )
            self._repo.set_content_tokens(message_id, token_indexes)
        except Exception as exc:
            logger.debug(f"Failed to refresh search tokens for {message_id}: {exc}")

        try:
            search_index_message(
                message_id,
                content_result.sanitized_content,
                {
                    "author_id": user_id,
                    "conversation_id": msg_row["conversation_id"],
                    "created_at": msg_row["created_at"],
                    "source_updated_at": now,
                },
            )
        except Exception as e:
            logger.debug(f"Failed to update search index for message {message_id}: {e}")

        cache_delete(f"msg:obj:{message_id}")

        msg = self.get_message(user_id, message_id)
        if msg is None:
            raise MessageNotFoundError("Failed to retrieve updated message")

        cache_set(f"msg:obj:{message_id}", msg, ttl=3600)
        return msg

    def update_message_metadata(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        metadata: Optional[Dict[str, Any]],
        merge: bool = True,
    ) -> Message:
        """Update message metadata."""
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row or msg_row.get("deleted"):
            raise MessageNotFoundError("Message not found")

        if msg_row["author_id"] != user_id:
            raise MessageAccessDeniedError("Can only update metadata on own messages")

        existing_metadata = (
            self._repo._json_loads(msg_row["metadata"])
            if msg_row.get("metadata")
            else None
        )
        if merge and existing_metadata and metadata:
            merged = {**existing_metadata, **metadata}
        elif merge and existing_metadata and metadata is None:
            merged = existing_metadata
        else:
            merged = metadata

        now = self._get_timestamp()
        self._repo.update_metadata(message_id, merged, now)

        cache_delete(f"msg:obj:{message_id}")

        msg = self.get_message(user_id, message_id)
        if msg is None:
            raise MessageNotFoundError("Failed to retrieve updated message")
        cache_set(f"msg:obj:{message_id}", msg, ttl=3600)
        return msg

    def automod_remove_message(
        self,
        message_id: SnowflakeID,
        violation_id: Optional[SnowflakeID] = None,
        rule_name: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Optional[Message]:
        """Replace a message with an AutoMod removal placeholder.

        Called by the AutoMod engine (async AI moderation) to 'delete' a
        message by replacing its content. Rendering is driven by the
        ``automod_removed`` metadata flag, never by content text, so users
        typing the placeholder phrase are unaffected. The stored violation id
        lets admins deep-link to the matching audit-log entry.

        This is a system-level operation: it intentionally bypasses the
        per-user edit/delete permission checks. The AutoMod boundary enforces
        ``__system_context__`` before invoking it.
        """
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row:
            return None

        now = self._get_timestamp()
        existing_metadata = (
            self._repo._json_loads(msg_row.get("metadata"))
            if msg_row.get("metadata")
            else {}
        )
        existing_metadata.update(
            {
                "automod_removed": True,
                "automod_violation_id": violation_id,
                "automod_rule": rule_name,
                "automod_reason": reason,
                "automod_removed_at": now,
            }
        )

        # mark_edited=False: a system removal is NOT a user edit and must
        # never surface an "edited" indicator to clients.
        self._repo.update_content(
            message_id,
            "",
            now,
            content_index="",
            mark_edited=False,
        )
        self._repo.update_metadata(message_id, existing_metadata, now)

        # Blank the search tokens so the removed content stops matching.
        try:
            self._repo.set_content_tokens(message_id, [])
        except Exception as exc:
            logger.debug(f"Failed to clear search tokens for {message_id}: {exc}")
        try:
            search_remove_from_index(message_id)
        except Exception as e:
            logger.debug(f"Failed to update search index for {message_id}: {e}")

        cache_delete(f"msg:obj:{message_id}")

        # Build the updated message directly (system call: no participant check).
        try:
            updated_row = self._repo.get_by_id(message_id)
            if not updated_row:
                return None
            pin_info = self._pin_repo.get_by_message(message_id)
            msg = self._repo.row_to_model(updated_row, pin_info)
            msg.attachments = self._attachment_repo.get_by_message(message_id)
        except Exception as exc:
            logger.warning(
                f"Failed to reload message {message_id} after removal: {exc}"
            )
            return None
        cache_set(f"msg:obj:{message_id}", msg, ttl=3600)
        return msg

    def delete_message(
        self, user_id: SnowflakeID, message_id: SnowflakeID, hard_delete: bool = False
    ) -> bool:
        """Delete a message.

        Authorization: the message author, a conversation OWNER/ADMIN, or —
        when the conversation is backed by a server channel — a user holding
        the server ``messages.manage`` permission on that channel.
        """
        msg_row = self._repo.get_by_id(message_id)
        if not msg_row:
            raise MessageNotFoundError("Message not found")

        can_delete = msg_row["author_id"] == user_id
        if not can_delete:
            participant = self._participant_svc.get_participant(
                msg_row["conversation_id"], user_id
            )
            if participant and participant.role in [
                ParticipantRole.OWNER,
                ParticipantRole.ADMIN,
            ]:
                can_delete = True
        if not can_delete and _is_server_message_moderator(
            user_id, msg_row["conversation_id"]
        ):
            can_delete = True

        if not can_delete:
            raise MessageAccessDeniedError("Cannot delete this message")

        now = self._get_timestamp()
        if hard_delete:
            self._repo.hard_delete(message_id)
            from src.utils.encryption.channel_ratchet import (
                ratchet_encryption_licensed,
            )

            if self._ratchet_manager is not None and ratchet_encryption_licensed():
                try:
                    new_interval = self._ratchet_manager.split_on_delete(
                        conversation_id=msg_row["conversation_id"],
                        deleted_message_id=message_id,
                        now_ms=now,
                    )
                    if new_interval is not None:
                        try:
                            from src.utils.encryption.channel_ratchet import (
                                notify_ratchet_update,
                            )

                            notify_ratchet_update(
                                conversation_id=int(msg_row["conversation_id"]),
                                update_data={
                                    "reason": "split",
                                    "new_interval_id": int(new_interval.interval_id),
                                    "at_message_id": int(new_interval.start_message_id),
                                    "deleted_message_id": int(message_id),
                                    "at": int(now),
                                },
                            )
                        except Exception as e:
                            logger.debug(
                                f"Ratchet split broadcast scheduling failed: {e}"
                            )
                except Exception as e:
                    logger.debug(f"Ratchet split-on-delete failed: {e}")
        else:
            self._repo.soft_delete(message_id, now)

        try:
            search_remove_from_index(message_id)
        except Exception as e:
            logger.debug(
                f"Failed to remove message {message_id} from search index: {e}"
            )

        cache_delete(f"msg:obj:{message_id}")

        try:
            from src.core.database import get_valkey_client as get_client

            client = get_client()
            if client:
                client.delete(f"recent_messages:{msg_row['conversation_id']}")
        except Exception:
            pass

        self._cleanup_thread_links(message_id)

        return True

    def delete_messages_bulk(
        self,
        user_id: SnowflakeID,
        message_ids: List[SnowflakeID],
        hard_delete: bool = False,
        conversation_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, Any]:
        """Delete multiple messages in bulk.

        When ``conversation_id`` (the requested channel) is provided every
        candidate message is scoped to that channel: only messages whose
        ``conversation_id`` matches the requested conversation — or the
        conversation linked to the requested server channel — are eligible.
        Messages belonging to any other conversation are reported as failed
        even if the caller could otherwise delete them.

        Authorization per message: author match, conversation OWNER/ADMIN,
        or server ``messages.manage`` on the backing server channel.

        Request size is capped at ``BULK_DELETE_MAX_IDS`` (100), matching
        the BulkDeleteRequest schema limit.
        """
        if len(message_ids) > BULK_DELETE_MAX_IDS:
            raise InvalidContentError(
                f"Cannot bulk-delete more than {BULK_DELETE_MAX_IDS} messages"
            )

        scope: Optional[set] = None
        if conversation_id is not None:
            scope = _resolve_channel_conversation_scope(int(conversation_id))

        success_count = 0
        deleted_ids: List[SnowflakeID] = []
        failed_ids: List[SnowflakeID] = []
        now = self._get_timestamp()

        for message_id in message_ids:
            try:
                msg_row = self._repo.get_by_id(message_id)
                if not msg_row:
                    failed_ids.append(message_id)
                    continue

                if scope is not None and int(msg_row["conversation_id"]) not in scope:
                    failed_ids.append(message_id)
                    continue

                can_delete = msg_row["author_id"] == user_id
                if not can_delete:
                    participant = self._participant_svc.get_participant(
                        msg_row["conversation_id"], user_id
                    )
                    if participant and participant.role in [
                        ParticipantRole.OWNER,
                        ParticipantRole.ADMIN,
                    ]:
                        can_delete = True
                if not can_delete and _is_server_message_moderator(
                    user_id, msg_row["conversation_id"]
                ):
                    can_delete = True

                if not can_delete:
                    failed_ids.append(message_id)
                    continue

                if hard_delete:
                    self._repo.hard_delete(message_id)
                else:
                    self._repo.soft_delete(message_id, now)

                # Cleanup parity with single delete_message: soft-deleted
                # messages must leave the search index and the per-conversation
                # recent-messages cache too, or they stay searchable and can
                # be resurrected from cache until TTL.
                try:
                    search_remove_from_index(message_id)
                except Exception as e:
                    logger.debug(
                        f"Failed to remove message {message_id} from search index: {e}"
                    )

                cache_delete(f"msg:obj:{message_id}")

                try:
                    from src.core.database import get_valkey_client as get_client

                    client = get_client()
                    if client:
                        client.delete(f"recent_messages:{msg_row['conversation_id']}")
                except Exception:
                    pass

                self._cleanup_thread_links(message_id)

                success_count += 1
                deleted_ids.append(message_id)
            except Exception as e:
                logger.error(f"Failed to delete message {message_id}: {e}")
                failed_ids.append(message_id)

        return {
            "success_count": success_count,
            "deleted_ids": deleted_ids,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
        }

    def archive_messages_bulk(
        self,
        user_id: SnowflakeID,
        message_ids: List[SnowflakeID],
    ) -> Dict[str, Any]:
        """Archive multiple messages."""
        import json

        success_count = 0
        failed_ids: List[SnowflakeID] = []
        now = self._get_timestamp()

        for message_id in message_ids:
            try:
                msg_row = self._repo.get_by_id(message_id)
                if not msg_row:
                    failed_ids.append(message_id)
                    continue

                can_archive = False
                participant = self._participant_svc.get_participant(
                    msg_row["conversation_id"], user_id
                )
                if participant and participant.role in [
                    ParticipantRole.OWNER,
                    ParticipantRole.ADMIN,
                ]:
                    can_archive = True

                if not can_archive:
                    failed_ids.append(message_id)
                    continue

                metadata: Dict[str, Any] = {}
                if msg_row.get("metadata"):
                    try:
                        metadata = json.loads(msg_row["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass

                metadata["archived"] = True
                metadata["archived_by"] = user_id
                metadata["archived_at"] = now

                self._repo.update_metadata(message_id, metadata, now)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to archive message {message_id}: {e}")
                failed_ids.append(message_id)

        return {
            "success_count": success_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
        }
