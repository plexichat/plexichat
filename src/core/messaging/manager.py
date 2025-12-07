"""
Messaging manager - Core business logic for messaging operations.

Handles all messaging operations with proper validation, permission checks,
and database interactions.
"""

import time
import json
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.utils.encryption import encrypt_data, decrypt_data, generate_snowflake_id

from .models import (
    Message,
    Conversation,
    Participant,
    MessageStatus,
    Attachment,
    ContentFilter,
    UserMessageSettings,
    ConversationType,
    MessageType,
    MessageStatusType,
    ParticipantRole,
    FilterAction,
)
from .exceptions import (
    MessagingError,
    ConversationNotFoundError,
    ConversationAccessDeniedError,
    MessageNotFoundError,
    MessageAccessDeniedError,
    ParticipantNotFoundError,
    ParticipantExistsError,
    ParticipantLimitError,
    InvalidContentError,
    ContentTooLongError,
    AttachmentError,
    AttachmentTooLargeError,
    AttachmentLimitError,
    InvalidRecipientError,
    ConversationTypeError,
)
from .schema import create_tables
from .content import validate_content, create_preview


class MessagingManager:
    """Core messaging manager handling all operations."""
    
    def __init__(self, db, auth_module=None):
        """
        Initialize the messaging manager.
        
        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for permission checks
        """
        self._db = db
        self._auth = auth_module
        self._config = self._load_config()
        
        # In-memory caches with TTL (reduces DB queries significantly)
        self._user_settings_cache: Dict[int, Tuple[UserMessageSettings, float]] = {}
        self._user_filter_cache: Dict[int, Tuple[ContentFilter, float]] = {}
        self._participant_cache: Dict[Tuple[int, int], Tuple[bool, float]] = {}
        self._cache_ttl = 60.0  # 60 second cache TTL
        
        # Create tables
        create_tables(db)
        
        logger.info("Messaging module initialized")
    
    def _cache_get(self, cache: dict, key, default=None):
        """Get value from cache if not expired."""
        if key in cache:
            value, expires = cache[key]
            if time.time() < expires:
                return value
            del cache[key]
        return default
    
    def _cache_set(self, cache: dict, key, value):
        """Set value in cache with TTL."""
        cache[key] = (value, time.time() + self._cache_ttl)
    
    def _cache_invalidate(self, cache: dict, key):
        """Invalidate a cache entry."""
        cache.pop(key, None)
    
    def _load_config(self) -> Dict[str, Any]:
        """Load messaging configuration."""
        defaults = {
            "max_message_length": 4000,
            "max_group_participants": 100,
            "max_attachment_size": 10485760,  # 10MB
            "max_attachments_per_message": 10,
            "dm_auto_create": True,
            "encrypt_messages": True,
            "encrypt_attachments": True,
            "message_preview_length": 100,
        }
        
        messaging_config = config.get("messaging", {})
        return {**defaults, **messaging_config}
    
    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)
    
    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()
    
    def _check_permission(self, user_id: int, permission: str) -> bool:
        """Check if user has a permission via auth module."""
        if not self._auth:
            return True  # No auth module, allow all
        
        # This would integrate with auth.has_permission
        # For now, return True - actual integration depends on token context
        return True
    
    # === Conversations ===
    
    def create_dm(
        self,
        user_id: int,
        recipient_id: int,
        auto_create: Optional[bool] = None
    ) -> Conversation:
        """Create or get existing DM conversation."""
        if user_id == recipient_id:
            raise InvalidRecipientError("Cannot create DM with yourself")
        
        # Check recipient's DM settings FIRST - applies to both new and existing DMs
        recipient_settings = self.get_user_message_settings(recipient_id)
        if recipient_settings.allow_dms_from == "none":
            raise ConversationAccessDeniedError("Recipient does not accept DMs")
        
        # Check if DM already exists
        existing = self._get_existing_dm(user_id, recipient_id)
        if existing:
            return existing
        
        # Check auto-create setting
        should_create = auto_create if auto_create is not None else self._config.get("dm_auto_create", True)
        
        if not should_create:
            raise ConversationNotFoundError("DM does not exist and auto-create is disabled")
        
        # Create new DM
        now = self._get_timestamp()
        conv_id = self._generate_id()
        
        self._db.execute(
            """INSERT INTO msg_conversations 
               (id, conversation_type, created_at, updated_at, max_participants, encrypted)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conv_id, ConversationType.DM.value, now, now, 2, 
             1 if self._config.get("encrypt_messages") else 0)
        )
        
        # Add participants
        for uid in [user_id, recipient_id]:
            part_id = self._generate_id()
            self._db.execute(
                """INSERT INTO msg_participants 
                   (id, conversation_id, user_id, role, joined_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (part_id, conv_id, uid, ParticipantRole.MEMBER.value, now)
            )
        
        # Add to DM lookup (store with smaller ID first for consistency)
        user1, user2 = min(user_id, recipient_id), max(user_id, recipient_id)
        lookup_id = self._generate_id()
        self._db.execute(
            """INSERT INTO msg_dm_lookup (id, user1_id, user2_id, conversation_id)
               VALUES (?, ?, ?, ?)""",
            (lookup_id, user1, user2, conv_id)
        )
        
        logger.debug(f"Created DM conversation {conv_id} between {user_id} and {recipient_id}")
        
        conversation = self.get_conversation(conv_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError(f"Failed to retrieve created conversation {conv_id}")
        return conversation
    
    def _get_existing_dm(self, user_id: int, recipient_id: int) -> Optional[Conversation]:
        """Get existing DM between two users."""
        user1, user2 = min(user_id, recipient_id), max(user_id, recipient_id)
        
        row = self._db.fetch_one(
            """SELECT conversation_id FROM msg_dm_lookup 
               WHERE user1_id = ? AND user2_id = ?""",
            (user1, user2)
        )
        
        if row:
            return self.get_conversation(row["conversation_id"], user_id)
        return None
    
    def create_group(
        self,
        owner_id: int,
        name: str,
        participant_ids: Optional[List[int]] = None,
        max_participants: Optional[int] = None
    ) -> Conversation:
        """Create a group conversation."""
        # Validate name
        if not name or not name.strip():
            raise InvalidContentError("Group name cannot be empty")
        
        name = name.strip()
        if len(name) > 100:
            raise InvalidContentError("Group name cannot exceed 100 characters")
        
        # Validate content
        content_result = validate_content(name)
        if not content_result.valid:
            raise InvalidContentError("Invalid group name", content_result.issues)
        
        max_parts = max_participants if max_participants is not None else self._config.get("max_group_participants", 100)
        
        # Check participant count - must have at least 1 (owner)
        participants = list(set([owner_id] + (participant_ids or [])))
        if max_parts < 1:
            raise ParticipantLimitError(
                "Group must allow at least 1 participant",
                max_parts, len(participants)
            )
        if len(participants) > max_parts:
            raise ParticipantLimitError(
                f"Cannot create group with more than {max_parts} participants",
                max_parts, len(participants)
            )
        
        now = self._get_timestamp()
        conv_id = self._generate_id()
        
        self._db.execute(
            """INSERT INTO msg_conversations 
               (id, conversation_type, name, owner_id, max_participants, created_at, updated_at, encrypted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (conv_id, ConversationType.GROUP.value, content_result.sanitized_content,
             owner_id, max_parts, now, now, 1 if self._config.get("encrypt_messages") else 0)
        )
        
        # Add participants
        for i, uid in enumerate(participants):
            part_id = self._generate_id()
            role = ParticipantRole.OWNER.value if uid == owner_id else ParticipantRole.MEMBER.value
            self._db.execute(
                """INSERT INTO msg_participants 
                   (id, conversation_id, user_id, role, joined_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (part_id, conv_id, uid, role, now)
            )
        
        # Send system message
        self.send_system_message(
            conv_id,
            f"Group \"{name}\" created",
            "group_created",
            {"creator_id": owner_id}
        )
        
        logger.debug(f"Created group conversation {conv_id} with {len(participants)} participants")
        
        conversation = self.get_conversation(conv_id, owner_id)
        if conversation is None:
            raise ConversationNotFoundError(f"Failed to retrieve created conversation {conv_id}")
        return conversation

    def create_server_channel_conversation(self, server_id: int, channel_id: int) -> Conversation:
        """Create a conversation for a server channel."""
        now = self._get_timestamp()
        conv_id = self._generate_id()
        
        self._db.execute(
            """INSERT INTO msg_conversations 
               (id, conversation_type, created_at, updated_at, encrypted, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conv_id, ConversationType.GROUP.value, now, now,
             1 if self._config.get("encrypt_messages") else 0,
             json.dumps({"server_id": server_id, "channel_id": channel_id}))
        )
        
        logger.debug(f"Created server channel conversation {conv_id} for channel {channel_id}")
        
        return Conversation(
            id=conv_id,
            conversation_type=ConversationType.GROUP,
            created_at=now,
            updated_at=now,
            encrypted=self._config.get("encrypt_messages", False)
        )
    
    def get_conversation(self, conversation_id: int, user_id: int) -> Optional[Conversation]:
        """Get a conversation by ID if user has access."""
        # Check access
        if not self._is_participant(conversation_id, user_id):
            return None
        
        row = self._db.fetch_one(
            """SELECT c.*, 
                      (SELECT COUNT(*) FROM msg_participants WHERE conversation_id = c.id) as participant_count
               FROM msg_conversations c
               WHERE c.id = ? AND c.deleted = 0""",
            (conversation_id,)
        )
        
        if not row:
            return None
        
        return self._row_to_conversation(row)
    
    def get_conversations(
        self,
        user_id: int,
        limit: int = 50,
        before_id: Optional[int] = None,
        conversation_type: Optional[ConversationType] = None
    ) -> List[Conversation]:
        """Get user's conversations with pagination."""
        limit = min(limit, 100)  # Cap at 100
        
        query = """
            SELECT c.*, 
                   (SELECT COUNT(*) FROM msg_participants WHERE conversation_id = c.id) as participant_count
            FROM msg_conversations c
            INNER JOIN msg_participants p ON c.id = p.conversation_id
            WHERE p.user_id = ? AND c.deleted = 0
        """
        params: List[Any] = [user_id]
        
        if conversation_type:
            query += " AND c.conversation_type = ?"
            params.append(conversation_type.value)
        
        if before_id:
            query += " AND c.id < ?"
            params.append(before_id)
        
        query += " ORDER BY COALESCE(c.last_message_at, c.created_at) DESC LIMIT ?"
        params.append(limit)
        
        rows = self._db.fetch_all(query, tuple(params))
        return [self._row_to_conversation(row) for row in rows]
    
    def update_conversation(
        self,
        user_id: int,
        conversation_id: int,
        name: Optional[str] = None,
        max_participants: Optional[int] = None
    ) -> Conversation:
        """Update conversation settings."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")
        
        if conv.conversation_type == ConversationType.DM:
            raise ConversationTypeError("Cannot update DM settings")
        
        # Check permission (owner or admin)
        participant = self._get_participant(conversation_id, user_id)
        assert participant is not None  # Checked by _is_participant above
        if participant.role not in [ParticipantRole.OWNER, ParticipantRole.ADMIN]:
            raise ConversationAccessDeniedError("Only owner or admin can update conversation")
        
        updates = []
        params = []
        
        if name is not None:
            name = name.strip()
            if not name:
                raise InvalidContentError("Group name cannot be empty")
            content_result = validate_content(name)
            if not content_result.valid:
                raise InvalidContentError("Invalid group name", content_result.issues)
            updates.append("name = ?")
            params.append(content_result.sanitized_content)
        
        if max_participants is not None:
            if max_participants < conv.participant_count:
                raise ParticipantLimitError(
                    "Cannot set max below current participant count",
                    max_participants, conv.participant_count
                )
            updates.append("max_participants = ?")
            params.append(max_participants)
        
        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(conversation_id)
            
            self._db.execute(
                f"UPDATE msg_conversations SET {', '.join(updates)} WHERE id = ?",
                tuple(params)
            )
        
        conversation = self.get_conversation(conversation_id, user_id)
        if conversation is None:
            raise ConversationNotFoundError(f"Failed to retrieve updated conversation {conversation_id}")
        return conversation
    
    def delete_conversation(self, user_id: int, conversation_id: int) -> bool:
        """Delete a conversation (soft delete)."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")
        
        # For DMs, either party can delete
        # For groups, only owner can delete
        if conv.conversation_type == ConversationType.GROUP:
            if conv.owner_id != user_id:
                raise ConversationAccessDeniedError("Only owner can delete group")
        
        now = self._get_timestamp()
        self._db.execute(
            "UPDATE msg_conversations SET deleted = 1, deleted_at = ? WHERE id = ?",
            (now, conversation_id)
        )
        
        # For DMs, also remove the lookup entry so a new DM can be created
        if conv.conversation_type == ConversationType.DM:
            self._db.execute(
                "DELETE FROM msg_dm_lookup WHERE conversation_id = ?",
                (conversation_id,)
            )
        
        logger.debug(f"Deleted conversation {conversation_id}")
        return True
    
    def leave_conversation(self, user_id: int, conversation_id: int) -> bool:
        """Leave a conversation."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")
        
        if conv.conversation_type == ConversationType.DM:
            # For DMs, leaving is same as deleting
            return self.delete_conversation(user_id, conversation_id)
        
        # For groups, remove participant
        participant = self._get_participant(conversation_id, user_id)
        assert participant is not None  # Checked by _is_participant above
        
        # If owner is leaving, transfer ownership or delete
        if participant.role == ParticipantRole.OWNER:
            # Find another admin or member to transfer to
            new_owner = self._db.fetch_one(
                """SELECT user_id FROM msg_participants 
                   WHERE conversation_id = ? AND user_id != ? 
                   ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, joined_at
                   LIMIT 1""",
                (conversation_id, user_id)
            )
            
            if new_owner:
                self._db.execute(
                    "UPDATE msg_participants SET role = ? WHERE conversation_id = ? AND user_id = ?",
                    (ParticipantRole.OWNER.value, conversation_id, new_owner["user_id"])
                )
                self._db.execute(
                    "UPDATE msg_conversations SET owner_id = ? WHERE id = ?",
                    (new_owner["user_id"], conversation_id)
                )
            else:
                # No one left, delete the conversation
                return self.delete_conversation(user_id, conversation_id)
        
        # Remove participant
        self._db.execute(
            "DELETE FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id)
        )
        
        # Send system message
        self.send_system_message(
            conversation_id,
            "A user left the conversation",
            "user_left",
            {"user_id": user_id}
        )
        
        return True
    
    # === Participants ===
    
    def add_participant(
        self,
        user_id: int,
        conversation_id: int,
        participant_id: int,
        role: ParticipantRole = ParticipantRole.MEMBER
    ) -> Participant:
        """Add a participant to a group conversation."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")
        
        if conv.conversation_type == ConversationType.DM:
            raise ConversationTypeError("Cannot add participants to DM")
        
        # Check permission
        actor = self._get_participant(conversation_id, user_id)
        assert actor is not None  # Checked by get_conversation above
        if actor.role not in [ParticipantRole.OWNER, ParticipantRole.ADMIN]:
            raise ConversationAccessDeniedError("Only owner or admin can add participants")
        
        # Check if already participant
        if self._is_participant(conversation_id, participant_id):
            raise ParticipantExistsError("User is already a participant")
        
        # Check participant limit
        if conv.participant_count >= conv.max_participants:
            raise ParticipantLimitError(
                "Conversation has reached maximum participants",
                conv.max_participants, conv.participant_count
            )
        
        # Cannot add as owner
        if role == ParticipantRole.OWNER:
            raise ConversationAccessDeniedError("Cannot add participant as owner")
        
        now = self._get_timestamp()
        part_id = self._generate_id()
        
        self._db.execute(
            """INSERT INTO msg_participants 
               (id, conversation_id, user_id, role, joined_at)
               VALUES (?, ?, ?, ?, ?)""",
            (part_id, conversation_id, participant_id, role.value, now)
        )
        
        # Send system message
        self.send_system_message(
            conversation_id,
            "A user was added to the conversation",
            "user_added",
            {"user_id": participant_id, "added_by": user_id}
        )
        
        result = self._get_participant(conversation_id, participant_id)
        assert result is not None  # Should exist since we just added it
        return result
    
    def remove_participant(
        self,
        user_id: int,
        conversation_id: int,
        participant_id: int
    ) -> bool:
        """Remove a participant from a group conversation."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")
        
        if conv.conversation_type == ConversationType.DM:
            raise ConversationTypeError("Cannot remove participants from DM")
        
        # Check permission
        actor = self._get_participant(conversation_id, user_id)
        assert actor is not None  # Checked by get_conversation above
        target = self._get_participant(conversation_id, participant_id)
        
        if not target:
            raise ParticipantNotFoundError("Participant not found")
        
        # Owner can remove anyone except themselves
        # Admin can remove members only
        if actor.role == ParticipantRole.OWNER:
            if participant_id == user_id:
                raise ConversationAccessDeniedError("Owner cannot remove themselves, use leave instead")
        elif actor.role == ParticipantRole.ADMIN:
            if target.role in [ParticipantRole.OWNER, ParticipantRole.ADMIN]:
                raise ConversationAccessDeniedError("Admin cannot remove owner or other admins")
        else:
            raise ConversationAccessDeniedError("Only owner or admin can remove participants")
        
        self._db.execute(
            "DELETE FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, participant_id)
        )
        
        # Send system message
        self.send_system_message(
            conversation_id,
            "A user was removed from the conversation",
            "user_removed",
            {"user_id": participant_id, "removed_by": user_id}
        )
        
        return True
    
    def update_participant_role(
        self,
        user_id: int,
        conversation_id: int,
        participant_id: int,
        role: ParticipantRole
    ) -> Participant:
        """Update a participant's role."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ConversationNotFoundError("Conversation not found")
        
        if conv.conversation_type == ConversationType.DM:
            raise ConversationTypeError("Cannot change roles in DM")
        
        actor = self._get_participant(conversation_id, user_id)
        assert actor is not None  # Checked by get_conversation above
        target = self._get_participant(conversation_id, participant_id)
        
        if not target:
            raise ParticipantNotFoundError("Participant not found")
        
        # Only owner can change roles
        if actor.role != ParticipantRole.OWNER:
            raise ConversationAccessDeniedError("Only owner can change roles")
        
        # Cannot change own role
        if user_id == participant_id:
            raise ConversationAccessDeniedError("Cannot change own role")
        
        # Cannot make someone else owner (use transfer ownership instead)
        if role == ParticipantRole.OWNER:
            raise ConversationAccessDeniedError("Cannot assign owner role, use transfer ownership")
        
        self._db.execute(
            "UPDATE msg_participants SET role = ? WHERE conversation_id = ? AND user_id = ?",
            (role.value, conversation_id, participant_id)
        )
        
        result = self._get_participant(conversation_id, participant_id)
        assert result is not None  # Should exist since we just updated it
        return result
    
    def get_participants(self, user_id: int, conversation_id: int) -> List[Participant]:
        """Get all participants in a conversation."""
        if not self._is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")
        
        rows = self._db.fetch_all(
            "SELECT * FROM msg_participants WHERE conversation_id = ? ORDER BY joined_at",
            (conversation_id,)
        )
        
        return [self._row_to_participant(row) for row in rows]
    
    def mute_conversation(
        self,
        user_id: int,
        conversation_id: int,
        muted: bool = True,
        until: Optional[int] = None
    ) -> bool:
        """Mute or unmute a conversation for a user."""
        if not self._is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")
        
        self._db.execute(
            "UPDATE msg_participants SET muted = ?, muted_until = ? WHERE conversation_id = ? AND user_id = ?",
            (1 if muted else 0, until, conversation_id, user_id)
        )
        
        return True
    
    def _is_participant(self, conversation_id: int, user_id: int) -> bool:
        """Check if user is a participant in conversation (cached)."""
        cache_key = (conversation_id, user_id)
        
        # Check cache first
        cached = self._cache_get(self._participant_cache, cache_key)
        if cached is not None:
            return cached
        
        # Optimized single query: check direct participant OR server membership
        # This combines both checks into one database round-trip
        row = self._db.fetch_one(
            """SELECT 
                CASE 
                    WHEN EXISTS (SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?) THEN 1
                    ELSE 0
                END as is_direct_participant,
                c.metadata
            FROM msg_conversations c WHERE c.id = ?""",
            (conversation_id, user_id, conversation_id)
        )
        
        if not row:
            self._cache_set(self._participant_cache, cache_key, False)
            return False
        
        # Check direct participant first (most common case)
        if row.get("is_direct_participant") == 1:
            self._cache_set(self._participant_cache, cache_key, True)
            return True
        
        # Check server membership if this is a server channel
        metadata = row.get("metadata")
        if metadata:
            try:
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                server_id = metadata.get("server_id")
                if server_id:
                    member_row = self._db.fetch_one(
                        "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
                        (int(server_id), user_id)
                    )
                    if member_row:
                        self._cache_set(self._participant_cache, cache_key, True)
                        return True
            except (json.JSONDecodeError, TypeError, ValueError):
                pass  # Invalid metadata, not a server channel
        
        self._cache_set(self._participant_cache, cache_key, False)
        return False
    
    def _get_participant(self, conversation_id: int, user_id: int) -> Optional[Participant]:
        """Get participant record."""
        row = self._db.fetch_one(
            "SELECT * FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id)
        )
        return self._row_to_participant(row) if row else None

    # === Messages ===
    
    def send_message(
        self,
        user_id: int,
        conversation_id: int,
        content: str,
        message_type: MessageType = MessageType.TEXT,
        reply_to_id: Optional[int] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> Message:
        """Send a message to a conversation."""
        # Check access
        if not self._is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")
        
        # Get user settings for limits
        user_settings = self.get_user_message_settings(user_id)
        max_length = user_settings.max_message_length or self._config.get("max_message_length", 4000)
        
        # Validate content
        user_filter = self.get_user_filter_settings(user_id)
        content_result = validate_content(content, user_filter, max_length)
        
        if not content_result.valid:
            if any("exceeds maximum length" in issue for issue in content_result.issues):
                raise ContentTooLongError(
                    f"Message exceeds maximum length of {max_length}",
                    max_length, len(content)
                )
            raise InvalidContentError("Invalid message content", content_result.issues)
        
        # Validate reply_to if provided
        if reply_to_id:
            reply_msg = self._db.fetch_one(
                "SELECT conversation_id FROM msg_messages WHERE id = ? AND deleted = 0",
                (reply_to_id,)
            )
            if not reply_msg or reply_msg["conversation_id"] != conversation_id:
                raise MessageNotFoundError("Reply target message not found in this conversation")
        
        # Validate attachments
        if attachments:
            max_attachments = user_settings.max_attachments_per_message or self._config.get("max_attachments_per_message", 10)
            if len(attachments) > max_attachments:
                raise AttachmentLimitError(
                    f"Cannot attach more than {max_attachments} files",
                    max_attachments, len(attachments)
                )
        
        now = self._get_timestamp()
        msg_id = self._generate_id()
        
        # Encrypt content if enabled
        final_content = content_result.sanitized_content
        encrypted_content = None
        
        # Only check encryption flag - avoid full get_conversation call
        if self._config.get("encrypt_messages"):
            conv_row = self._db.fetch_one(
                "SELECT encrypted FROM msg_conversations WHERE id = ?",
                (conversation_id,)
            )
            if conv_row and conv_row.get("encrypted"):
                encrypted_content = encrypt_data(final_content)
                final_content = "[encrypted]"
        
        # Build metadata
        metadata = {}
        if content_result.has_spoilers:
            metadata["has_spoilers"] = True
        if content_result.has_nsfw:
            metadata["has_nsfw"] = True
        if content_result.filtered_words:
            metadata["filtered"] = True
        
        self._db.execute(
            """INSERT INTO msg_messages 
               (id, conversation_id, author_id, content, content_encrypted, message_type, 
                created_at, updated_at, reply_to_id, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, conversation_id, user_id, final_content, encrypted_content,
             message_type.value, now, now, reply_to_id, json.dumps(metadata) if metadata else None)
        )
        
        # Update conversation
        self._db.execute(
            "UPDATE msg_conversations SET last_message_id = ?, last_message_at = ?, updated_at = ? WHERE id = ?",
            (msg_id, now, now, conversation_id)
        )
        
        # Add attachments
        if attachments:
            for att_data in attachments:
                self.add_attachment(
                    user_id, msg_id,
                    att_data.get("filename", "file"),
                    att_data.get("content_type", "application/octet-stream"),
                    att_data.get("size", 0),
                    att_data.get("url", ""),
                    att_data.get("metadata")
                )
        
        # Create initial status (sent)
        status_id = self._generate_id()
        self._db.execute(
            """INSERT INTO msg_message_status (id, message_id, user_id, status, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (status_id, msg_id, user_id, MessageStatusType.SENT.value, now)
        )
        
        logger.debug(f"Message {msg_id} sent to conversation {conversation_id}")
        
        # Build Message object directly instead of re-fetching from DB
        # This avoids redundant _is_participant check and DB query
        attachment_list = []
        if attachments:
            for att_data in attachments:
                attachment_list.append(Attachment(
                    id=self._generate_id(),  # Approximate - actual ID from add_attachment
                    message_id=msg_id,
                    filename=att_data.get("filename", "file"),
                    content_type=att_data.get("content_type", "application/octet-stream"),
                    size=att_data.get("size", 0),
                    url=att_data.get("url", ""),
                    uploaded_at=now
                ))
        
        # Return the original sanitized content (not "[encrypted]") so the sender sees their message
        # The encrypted_content field contains the actual encrypted data for storage
        return Message(
            id=msg_id,
            conversation_id=conversation_id,
            author_id=user_id,
            content=content_result.sanitized_content,  # Return original content, not "[encrypted]"
            content_encrypted=encrypted_content,
            message_type=message_type,
            created_at=now,
            updated_at=now,
            reply_to_id=reply_to_id,
            edited=False,
            deleted=False,
            pinned=False,
            metadata=metadata if metadata else None,
            attachments=attachment_list
        )
    
    def edit_message(self, user_id: int, message_id: int, content: str) -> Message:
        """Edit a message (own messages only)."""
        msg = self._get_message_raw(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")
        
        if msg["deleted"]:
            raise MessageNotFoundError("Message not found")
        
        # Must be participant in conversation to edit
        if not self._is_participant(msg["conversation_id"], user_id):
            raise MessageNotFoundError("Message not found")
        
        if msg["author_id"] != user_id:
            raise MessageAccessDeniedError("Can only edit own messages")
        
        # Validate content
        user_settings = self.get_user_message_settings(user_id)
        max_length = user_settings.max_message_length or self._config.get("max_message_length", 4000)
        user_filter = self.get_user_filter_settings(user_id)
        
        content_result = validate_content(content, user_filter, max_length)
        if not content_result.valid:
            raise InvalidContentError("Invalid message content", content_result.issues)
        
        now = self._get_timestamp()
        final_content = content_result.sanitized_content
        encrypted_content = None
        
        # Check if conversation is encrypted
        conv = self._db.fetch_one(
            "SELECT encrypted FROM msg_conversations WHERE id = ?",
            (msg["conversation_id"],)
        )
        if conv and conv["encrypted"] and self._config.get("encrypt_messages"):
            encrypted_content = encrypt_data(final_content)
            final_content = "[encrypted]"
        
        self._db.execute(
            """UPDATE msg_messages 
               SET content = ?, content_encrypted = ?, updated_at = ?, edited = 1
               WHERE id = ?""",
            (final_content, encrypted_content, now, message_id)
        )
        
        result = self.get_message(user_id, message_id)
        assert result is not None  # Should exist since we just updated it
        return result
    
    def delete_message(self, user_id: int, message_id: int, hard_delete: bool = False) -> bool:
        """Delete a message."""
        msg = self._get_message_raw(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")
        
        # Check permission - own message or admin in conversation
        can_delete = msg["author_id"] == user_id
        
        if not can_delete:
            participant = self._get_participant(msg["conversation_id"], user_id)
            if participant and participant.role in [ParticipantRole.OWNER, ParticipantRole.ADMIN]:
                can_delete = True
        
        if not can_delete:
            raise MessageAccessDeniedError("Cannot delete this message")
        
        now = self._get_timestamp()
        
        if hard_delete:
            # Hard delete - actually remove from database
            self._db.execute("DELETE FROM msg_messages WHERE id = ?", (message_id,))
        else:
            # Soft delete
            self._db.execute(
                "UPDATE msg_messages SET deleted = 1, deleted_at = ?, content = '[deleted]' WHERE id = ?",
                (now, message_id)
            )
        
        return True
    
    def get_message(self, user_id: int, message_id: int) -> Optional[Message]:
        """Get a single message by ID."""
        msg = self._get_message_raw(message_id)
        if not msg:
            return None
        
        # Deleted messages are not accessible via API
        if msg["deleted"]:
            return None
        
        # Check access
        if not self._is_participant(msg["conversation_id"], user_id):
            return None
        
        return self._row_to_message(msg)
    
    def get_messages(
        self,
        user_id: int,
        conversation_id: int,
        limit: int = 50,
        before_id: Optional[int] = None,
        after_id: Optional[int] = None
    ) -> List[Message]:
        """Get messages from a conversation with cursor pagination."""
        if not self._is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")
        
        limit = min(limit, 100)
        
        query = "SELECT * FROM msg_messages WHERE conversation_id = ? AND deleted = 0"
        params = [conversation_id]
        
        if before_id:
            query += " AND id < ?"
            params.append(before_id)
            query += " ORDER BY id DESC"
        elif after_id:
            query += " AND id > ?"
            params.append(after_id)
            query += " ORDER BY id ASC"
        else:
            query += " ORDER BY id DESC"
        
        query += " LIMIT ?"
        params.append(limit)
        
        rows = self._db.fetch_all(query, tuple(params))
        
        messages = [self._row_to_message(row) for row in rows]
        
        # Batch load attachments for all messages in a single query (fixes N+1)
        if messages:
            message_ids = [msg.id for msg in messages]
            attachments_map = self._get_attachments_batch(message_ids)
            for msg in messages:
                msg.attachments = attachments_map.get(msg.id, [])
        
        return messages
    
    def _get_attachments_batch(self, message_ids: List[int]) -> Dict[int, List[Attachment]]:
        """Batch load attachments for multiple messages (avoids N+1 queries)."""
        if not message_ids:
            return {}
        
        placeholders = ",".join("?" * len(message_ids))
        rows = self._db.fetch_all(
            f"SELECT * FROM msg_attachments WHERE message_id IN ({placeholders}) AND deleted = 0",
            tuple(message_ids)
        )
        
        result: Dict[int, List[Attachment]] = {mid: [] for mid in message_ids}
        for row in rows:
            att = self._row_to_attachment(row)
            result[att.message_id].append(att)
        
        return result
    
    def pin_message(self, user_id: int, message_id: int) -> bool:
        """Pin a message in its conversation."""
        msg = self._get_message_raw(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")
        
        # Cannot pin deleted messages
        if msg["deleted"]:
            raise MessageNotFoundError("Message not found")
        
        if not self._is_participant(msg["conversation_id"], user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")
        
        # Check if already pinned
        existing = self._db.fetch_one(
            "SELECT 1 FROM msg_pinned WHERE message_id = ?",
            (message_id,)
        )
        if existing:
            return True  # Already pinned
        
        now = self._get_timestamp()
        pin_id = self._generate_id()
        
        self._db.execute(
            """INSERT INTO msg_pinned (id, conversation_id, message_id, pinned_by, pinned_at)
               VALUES (?, ?, ?, ?, ?)""",
            (pin_id, msg["conversation_id"], message_id, user_id, now)
        )
        
        return True
    
    def unpin_message(self, user_id: int, message_id: int) -> bool:
        """Unpin a message."""
        msg = self._get_message_raw(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")
        
        if not self._is_participant(msg["conversation_id"], user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")
        
        self._db.execute("DELETE FROM msg_pinned WHERE message_id = ?", (message_id,))
        return True
    
    def get_pinned_messages(self, user_id: int, conversation_id: int) -> List[Message]:
        """Get all pinned messages in a conversation."""
        if not self._is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")
        
        rows = self._db.fetch_all(
            """SELECT m.* FROM msg_messages m
               INNER JOIN msg_pinned p ON m.id = p.message_id
               WHERE p.conversation_id = ? AND m.deleted = 0
               ORDER BY p.pinned_at DESC""",
            (conversation_id,)
        )
        
        return [self._row_to_message(row) for row in rows]
    
    def _get_message_raw(self, message_id: int) -> Optional[Dict]:
        """Get raw message row from database."""
        return self._db.fetch_one(
            "SELECT * FROM msg_messages WHERE id = ?",
            (message_id,)
        )
    
    # === Message Status ===
    
    def mark_delivered(self, user_id: int, message_ids: List[int]) -> int:
        """Mark messages as delivered."""
        count = 0
        now = self._get_timestamp()
        
        for msg_id in message_ids:
            msg = self._get_message_raw(msg_id)
            if not msg or msg["author_id"] == user_id:
                continue
            
            if not self._is_participant(msg["conversation_id"], user_id):
                continue
            
            # Check if already has status
            existing = self._db.fetch_one(
                "SELECT status FROM msg_message_status WHERE message_id = ? AND user_id = ?",
                (msg_id, user_id)
            )
            
            if existing and existing["status"] in [MessageStatusType.DELIVERED.value, MessageStatusType.READ.value]:
                continue
            
            if existing:
                self._db.execute(
                    "UPDATE msg_message_status SET status = ?, timestamp = ? WHERE message_id = ? AND user_id = ?",
                    (MessageStatusType.DELIVERED.value, now, msg_id, user_id)
                )
            else:
                status_id = self._generate_id()
                self._db.execute(
                    """INSERT INTO msg_message_status (id, message_id, user_id, status, timestamp)
                       VALUES (?, ?, ?, ?, ?)""",
                    (status_id, msg_id, user_id, MessageStatusType.DELIVERED.value, now)
                )
            count += 1
        
        return count
    
    def mark_read(self, user_id: int, conversation_id: int, up_to_message_id: Optional[int] = None) -> int:
        """Mark messages as read (optimized batch operation)."""
        if not self._is_participant(conversation_id, user_id):
            raise ConversationAccessDeniedError("Not a participant in this conversation")
        
        now = self._get_timestamp()
        
        # Build the message filter
        msg_filter = "conversation_id = ? AND author_id != ? AND deleted = 0"
        params = [conversation_id, user_id]
        
        if up_to_message_id:
            msg_filter += " AND id <= ?"
            params.append(up_to_message_id)
        
        # Batch update existing statuses that aren't already READ
        self._db.execute(
            f"""UPDATE msg_message_status 
                SET status = ?, timestamp = ?
                WHERE user_id = ? AND status != ?
                AND message_id IN (SELECT id FROM msg_messages WHERE {msg_filter})""",
            (MessageStatusType.READ.value, now, user_id, MessageStatusType.READ.value) + tuple(params)
        )
        
        # Batch insert new statuses for messages without any status
        # Use INSERT OR IGNORE to handle race conditions
        self._db.execute(
            f"""INSERT OR IGNORE INTO msg_message_status (id, message_id, user_id, status, timestamp)
                SELECT 
                    abs(random()) % 9223372036854775807,
                    m.id,
                    ?,
                    ?,
                    ?
                FROM msg_messages m
                WHERE m.{msg_filter}
                AND NOT EXISTS (
                    SELECT 1 FROM msg_message_status s 
                    WHERE s.message_id = m.id AND s.user_id = ?
                )""",
            (user_id, MessageStatusType.READ.value, now) + tuple(params) + (user_id,)
        )
        
        # Get count of affected messages
        count_row = self._db.fetch_one(
            f"SELECT COUNT(*) as cnt FROM msg_messages WHERE {msg_filter}",
            tuple(params)
        )
        count = count_row["cnt"] if count_row else 0
        
        # Update participant's last read
        last_msg_id = up_to_message_id
        if not last_msg_id:
            last_row = self._db.fetch_one(
                "SELECT MAX(id) as max_id FROM msg_messages WHERE conversation_id = ? AND deleted = 0",
                (conversation_id,)
            )
            last_msg_id = last_row["max_id"] if last_row else None
        
        if last_msg_id:
            self._db.execute(
                "UPDATE msg_participants SET last_read_message_id = ?, last_read_at = ? WHERE conversation_id = ? AND user_id = ?",
                (last_msg_id, now, conversation_id, user_id)
            )
        
        return count
    
    def get_unread_count(self, user_id: int, conversation_id: Optional[int] = None) -> Dict[int, int]:
        """Get unread message counts (optimized batch query)."""
        result = {}
        
        if conversation_id:
            if not self._is_participant(conversation_id, user_id):
                return result
            # Single conversation - use optimized single query
            row = self._db.fetch_one(
                """SELECT 
                    p.conversation_id,
                    COALESCE(
                        (SELECT COUNT(*) FROM msg_messages m 
                         WHERE m.conversation_id = p.conversation_id 
                         AND m.author_id != ? 
                         AND m.deleted = 0
                         AND (p.last_read_message_id IS NULL OR m.id > p.last_read_message_id)
                        ), 0
                    ) as unread_count
                FROM msg_participants p
                WHERE p.conversation_id = ? AND p.user_id = ?""",
                (user_id, conversation_id, user_id)
            )
            if row:
                result[row["conversation_id"]] = row["unread_count"]
            return result
        
        # Batch query for all conversations (fixes N+1)
        rows = self._db.fetch_all(
            """SELECT 
                p.conversation_id,
                COALESCE(
                    (SELECT COUNT(*) FROM msg_messages m 
                     WHERE m.conversation_id = p.conversation_id 
                     AND m.author_id != ? 
                     AND m.deleted = 0
                     AND (p.last_read_message_id IS NULL OR m.id > p.last_read_message_id)
                    ), 0
                ) as unread_count
            FROM msg_participants p
            WHERE p.user_id = ?""",
            (user_id, user_id)
        )
        
        for row in rows:
            result[row["conversation_id"]] = row["unread_count"]
        
        return result
    
    def get_message_status(self, user_id: int, message_id: int) -> List[MessageStatus]:
        """Get delivery/read status for a message."""
        msg = self._get_message_raw(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")
        
        # Only sender can see status
        if msg["author_id"] != user_id:
            raise MessageAccessDeniedError("Only sender can view message status")
        
        rows = self._db.fetch_all(
            "SELECT * FROM msg_message_status WHERE message_id = ? ORDER BY timestamp",
            (message_id,)
        )
        
        return [self._row_to_message_status(row) for row in rows]

    # === Content Filtering ===
    
    def get_user_filter_settings(self, user_id: int) -> ContentFilter:
        """Get user's content filter settings (cached)."""
        # Check cache first
        cached = self._cache_get(self._user_filter_cache, user_id)
        if cached is not None:
            return cached
        
        row = self._db.fetch_one(
            "SELECT * FROM msg_content_filters WHERE user_id = ?",
            (user_id,)
        )
        
        if row:
            result = ContentFilter(
                user_id=user_id,
                profanity_filter=bool(row["profanity_filter"]),
                nsfw_filter=bool(row["nsfw_filter"]),
                spoiler_click_to_reveal=bool(row["spoiler_click_to_reveal"]),
                custom_blocked_words=json.loads(row["custom_blocked_words"]) if row["custom_blocked_words"] else [],
                filter_action=FilterAction(row["filter_action"]) if row["filter_action"] else FilterAction.CENSOR
            )
        else:
            # Return defaults
            result = ContentFilter(user_id=user_id)
        
        self._cache_set(self._user_filter_cache, user_id, result)
        return result
    
    def update_user_filter_settings(
        self,
        user_id: int,
        profanity_filter: Optional[bool] = None,
        nsfw_filter: Optional[bool] = None,
        spoiler_click_to_reveal: Optional[bool] = None,
        custom_blocked_words: Optional[List[str]] = None
    ) -> ContentFilter:
        """Update user's content filter settings."""
        current = self.get_user_filter_settings(user_id)
        
        # Check if record exists
        existing = self._db.fetch_one(
            "SELECT 1 FROM msg_content_filters WHERE user_id = ?",
            (user_id,)
        )
        
        new_profanity = profanity_filter if profanity_filter is not None else current.profanity_filter
        new_nsfw = nsfw_filter if nsfw_filter is not None else current.nsfw_filter
        new_spoiler = spoiler_click_to_reveal if spoiler_click_to_reveal is not None else current.spoiler_click_to_reveal
        new_words = custom_blocked_words if custom_blocked_words is not None else current.custom_blocked_words
        
        if existing:
            self._db.execute(
                """UPDATE msg_content_filters 
                   SET profanity_filter = ?, nsfw_filter = ?, spoiler_click_to_reveal = ?, custom_blocked_words = ?
                   WHERE user_id = ?""",
                (1 if new_profanity else 0, 1 if new_nsfw else 0, 1 if new_spoiler else 0,
                 json.dumps(new_words), user_id)
            )
        else:
            self._db.execute(
                """INSERT INTO msg_content_filters 
                   (user_id, profanity_filter, nsfw_filter, spoiler_click_to_reveal, custom_blocked_words)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, 1 if new_profanity else 0, 1 if new_nsfw else 0, 1 if new_spoiler else 0,
                 json.dumps(new_words))
            )
        
        return self.get_user_filter_settings(user_id)
    
    # === User Message Settings ===
    
    def get_user_message_settings(self, user_id: int) -> UserMessageSettings:
        """Get user's message settings (cached)."""
        # Check cache first
        cached = self._cache_get(self._user_settings_cache, user_id)
        if cached is not None:
            return cached
        
        row = self._db.fetch_one(
            "SELECT * FROM msg_user_settings WHERE user_id = ?",
            (user_id,)
        )
        
        if row:
            result = UserMessageSettings(
                user_id=user_id,
                allow_dms_from=row["allow_dms_from"] or "everyone",
                auto_create_dms=bool(row["auto_create_dms"]),
                max_message_length=row["max_message_length"],
                max_attachment_size=row["max_attachment_size"],
                max_attachments_per_message=row["max_attachments_per_message"],
                read_receipts_enabled=bool(row["read_receipts_enabled"]),
                typing_indicators_enabled=bool(row["typing_indicators_enabled"])
            )
        else:
            # Return defaults
            result = UserMessageSettings(user_id=user_id)
        
        self._cache_set(self._user_settings_cache, user_id, result)
        return result
    
    def update_user_message_settings(
        self,
        user_id: int,
        allow_dms_from: Optional[str] = None,
        auto_create_dms: Optional[bool] = None,
        max_message_length: Optional[int] = None,
        max_attachment_size: Optional[int] = None,
        max_attachments_per_message: Optional[int] = None
    ) -> UserMessageSettings:
        """Update user's message settings."""
        current = self.get_user_message_settings(user_id)
        
        existing = self._db.fetch_one(
            "SELECT 1 FROM msg_user_settings WHERE user_id = ?",
            (user_id,)
        )
        
        new_dms = allow_dms_from if allow_dms_from is not None else current.allow_dms_from
        new_auto = auto_create_dms if auto_create_dms is not None else current.auto_create_dms
        new_length = max_message_length if max_message_length is not None else current.max_message_length
        new_att_size = max_attachment_size if max_attachment_size is not None else current.max_attachment_size
        new_att_count = max_attachments_per_message if max_attachments_per_message is not None else current.max_attachments_per_message
        
        if existing:
            self._db.execute(
                """UPDATE msg_user_settings 
                   SET allow_dms_from = ?, auto_create_dms = ?, max_message_length = ?,
                       max_attachment_size = ?, max_attachments_per_message = ?
                   WHERE user_id = ?""",
                (new_dms, 1 if new_auto else 0, new_length, new_att_size, new_att_count, user_id)
            )
        else:
            self._db.execute(
                """INSERT INTO msg_user_settings 
                   (user_id, allow_dms_from, auto_create_dms, max_message_length, 
                    max_attachment_size, max_attachments_per_message)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, new_dms, 1 if new_auto else 0, new_length, new_att_size, new_att_count)
            )
        
        return self.get_user_message_settings(user_id)
    
    # === Attachments ===
    
    def add_attachment(
        self,
        user_id: int,
        message_id: int,
        filename: str,
        content_type: str,
        size: int,
        url: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Attachment:
        """Add an attachment to a message."""
        msg = self._get_message_raw(message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")
        
        if msg["author_id"] != user_id:
            raise MessageAccessDeniedError("Can only add attachments to own messages")
        
        # Check size limit
        user_settings = self.get_user_message_settings(user_id)
        max_size = user_settings.max_attachment_size or self._config.get("max_attachment_size", 10485760)
        
        if size > max_size:
            raise AttachmentTooLargeError(
                f"Attachment exceeds maximum size of {max_size} bytes",
                max_size, size
            )
        
        # Check attachment count
        existing_count = self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM msg_attachments WHERE message_id = ? AND deleted = 0",
            (message_id,)
        )
        max_count = user_settings.max_attachments_per_message or self._config.get("max_attachments_per_message", 10)
        
        if existing_count and existing_count["cnt"] >= max_count:
            raise AttachmentLimitError(
                f"Message already has maximum attachments ({max_count})",
                max_count, existing_count["cnt"]
            )
        
        now = self._get_timestamp()
        att_id = self._generate_id()
        
        # Encrypt URL if enabled
        encrypted_url = None
        if self._config.get("encrypt_attachments"):
            encrypted_url = encrypt_data(url)
            url = "[encrypted]"
        
        self._db.execute(
            """INSERT INTO msg_attachments 
               (id, message_id, filename, content_type, size, url, url_encrypted, created_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (att_id, message_id, filename, content_type, size, url, encrypted_url, now,
             json.dumps(metadata) if metadata else None)
        )
        
        result = self._get_attachment(att_id)
        assert result is not None  # Should exist since we just created it
        return result
    
    def get_attachments(self, user_id: int, message_id: int) -> List[Attachment]:
        """Get attachments for a message."""
        msg = self._get_message_raw(message_id)
        if not msg:
            return []
        
        if not self._is_participant(msg["conversation_id"], user_id):
            return []
        
        rows = self._db.fetch_all(
            "SELECT * FROM msg_attachments WHERE message_id = ? AND deleted = 0",
            (message_id,)
        )
        
        return [self._row_to_attachment(row) for row in rows]
    
    def delete_attachment(self, user_id: int, attachment_id: int) -> bool:
        """Delete an attachment."""
        att = self._get_attachment(attachment_id)
        if not att:
            raise AttachmentError("Attachment not found")
        
        msg = self._get_message_raw(att.message_id)
        if not msg or msg["author_id"] != user_id:
            raise MessageAccessDeniedError("Can only delete own attachments")
        
        self._db.execute(
            "UPDATE msg_attachments SET deleted = 1 WHERE id = ?",
            (attachment_id,)
        )
        
        return True
    
    def _get_attachment(self, attachment_id: int) -> Optional[Attachment]:
        """Get attachment by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM msg_attachments WHERE id = ? AND deleted = 0",
            (attachment_id,)
        )
        return self._row_to_attachment(row) if row else None
    
    # === System Messages ===
    
    def send_system_message(
        self,
        conversation_id: int,
        content: str,
        event_type: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Message:
        """Send a system message."""
        now = self._get_timestamp()
        msg_id = self._generate_id()
        
        full_metadata = {"event_type": event_type}
        if metadata:
            full_metadata.update(metadata)
        
        self._db.execute(
            """INSERT INTO msg_messages 
               (id, conversation_id, author_id, content, message_type, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, conversation_id, 0, content, MessageType.SYSTEM.value, now, now,
             json.dumps(full_metadata))
        )
        
        # Update conversation
        self._db.execute(
            "UPDATE msg_conversations SET last_message_id = ?, last_message_at = ?, updated_at = ? WHERE id = ?",
            (msg_id, now, now, conversation_id)
        )
        
        row = self._db.fetch_one("SELECT * FROM msg_messages WHERE id = ?", (msg_id,))
        return self._row_to_message(row)
    
    # === Row Converters ===
    
    def _row_to_conversation(self, row: Dict) -> Conversation:
        """Convert database row to Conversation model."""
        # Handle both dict and sqlite3.Row
        participant_count = 0
        try:
            participant_count = row["participant_count"]
        except (KeyError, IndexError):
            pass
        
        return Conversation(
            id=row["id"],
            conversation_type=ConversationType(row["conversation_type"]),
            name=row["name"],
            owner_id=row["owner_id"],
            max_participants=row["max_participants"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_message_id=row["last_message_id"],
            last_message_at=row["last_message_at"],
            encrypted=bool(row["encrypted"]),
            deleted=bool(row["deleted"]),
            deleted_at=row["deleted_at"],
            participant_count=participant_count,
            metadata=json.loads(row["metadata"]) if row["metadata"] else None
        )
    
    def _row_to_participant(self, row: Dict) -> Participant:
        """Convert database row to Participant model."""
        return Participant(
            id=row["id"],
            conversation_id=row["conversation_id"],
            user_id=row["user_id"],
            role=ParticipantRole(row["role"]),
            joined_at=row["joined_at"],
            last_read_message_id=row["last_read_message_id"],
            last_read_at=row["last_read_at"],
            muted=bool(row["muted"]),
            muted_until=row["muted_until"],
            permissions=json.loads(row["permissions"]) if row["permissions"] else None,
            nickname=row["nickname"]
        )
    
    def _row_to_message(self, row: Dict) -> Message:
        """Convert database row to Message model."""
        content = row["content"]
        
        # Decrypt if encrypted
        if row["content_encrypted"] and content == "[encrypted]":
            try:
                content = decrypt_data(row["content_encrypted"])
            except Exception:
                content = "[decryption failed]"
        
        # Get pin status
        pin_row = self._db.fetch_one(
            "SELECT pinned_by, pinned_at FROM msg_pinned WHERE message_id = ?",
            (row["id"],)
        )
        
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            author_id=row["author_id"],
            content=content,
            content_encrypted=row["content_encrypted"],
            message_type=MessageType(row["message_type"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            edited=bool(row["edited"]),
            deleted=bool(row["deleted"]),
            deleted_at=row["deleted_at"],
            reply_to_id=row["reply_to_id"],
            pinned=pin_row is not None,
            pinned_at=pin_row["pinned_at"] if pin_row else None,
            pinned_by=pin_row["pinned_by"] if pin_row else None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else None
        )
    
    def _row_to_message_status(self, row: Dict) -> MessageStatus:
        """Convert database row to MessageStatus model."""
        return MessageStatus(
            id=row["id"],
            message_id=row["message_id"],
            user_id=row["user_id"],
            status=MessageStatusType(row["status"]),
            timestamp=row["timestamp"]
        )
    
    def _row_to_attachment(self, row: Dict) -> Attachment:
        """Convert database row to Attachment model."""
        url = row["url"]
        
        # Decrypt if encrypted
        if row["url_encrypted"] and url == "[encrypted]":
            try:
                url = decrypt_data(row["url_encrypted"])
            except Exception:
                url = "[decryption failed]"
        
        return Attachment(
            id=row["id"],
            message_id=row["message_id"],
            filename=row["filename"],
            content_type=row["content_type"],
            size=row["size"],
            url=url,
            url_encrypted=row["url_encrypted"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            deleted=bool(row["deleted"])
        )
