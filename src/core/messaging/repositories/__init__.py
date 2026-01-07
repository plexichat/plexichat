"""
Messaging repositories - Data access layer.

Provides database abstraction for messaging operations.
Supports SQLite (single machine) and can be extended for PostgreSQL.
"""

from .base import BaseRepository
from .conversation import ConversationRepository
from .message import MessageRepository
from .participant import ParticipantRepository
from .message_status import MessageStatusRepository
from .attachment import AttachmentRepository
from .pin import PinRepository
from .user_settings import UserSettingsRepository

__all__ = [
    "BaseRepository",
    "ConversationRepository",
    "MessageRepository",
    "ParticipantRepository",
    "MessageStatusRepository",
    "AttachmentRepository",
    "PinRepository",
    "UserSettingsRepository",
]
