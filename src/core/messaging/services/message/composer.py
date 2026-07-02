"""
Composer for the MessageService.
Assembles all mixins into the final MessageService class via MRO.
"""

from typing import Any

from src.utils.encryption.channel_ratchet import ChannelRatchetManager

from .send import SendMixin
from .edit_delete import EditDeleteMixin
from .retrieval import RetrievalMixin
from ...repositories.message import MessageRepository
from ...repositories.attachment import AttachmentRepository
from ...repositories.pin import PinRepository
from ...repositories.conversation import ConversationRepository
from ...repositories.message_status import MessageStatusRepository


class MessageService(
    RetrievalMixin,
    EditDeleteMixin,
    SendMixin,
):
    """Complete MessageService assembled from mixins via MRO."""

    def __init__(
        self,
        db: Any,
        participant_service: Any,
        user_settings_service: Any,
        content_filter_service: Any,
        ratchet_manager: Any = None,
    ) -> None:
        """Initialize the message service with all dependencies.

        Args:
            db: Database instance
            participant_service: ParticipantService instance
            user_settings_service: UserSettingsService instance
            content_filter_service: ContentFilterService instance
            ratchet_manager: Optional pre-built
                :class:`ChannelRatchetManager`. When ``None``, a new
                manager bound to ``db`` is created.
        """
        super().__init__(db)
        self._repo = MessageRepository(db)
        self._attachment_repo = AttachmentRepository(db)
        self._pin_repo = PinRepository(db)
        self._conversation_repo = ConversationRepository(db)
        self._status_repo = MessageStatusRepository(db)
        self._participant_svc = participant_service
        self._user_settings_svc = user_settings_service
        self._content_filter_svc = content_filter_service
        self._ratchet_manager = ratchet_manager or ChannelRatchetManager(db)
