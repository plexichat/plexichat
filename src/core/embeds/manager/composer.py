"""
Embed manager composer - Combines all mixins into EmbedManager.
"""

import utils.logger as logger

from .attachment import EmbedAttachmentMixin
from .base import EmbedManagerBase
from .crud import EmbedCRUDMixin
from .url_preview import EmbedURLPreviewMixin
from .validation import EmbedValidationMixin


class EmbedManager(
    EmbedValidationMixin,
    EmbedCRUDMixin,
    EmbedAttachmentMixin,
    EmbedURLPreviewMixin,
    EmbedManagerBase,
):
    """
    Core embed manager handling all operations.

    Composed from:
    - EmbedValidationMixin: validate_embed, sanitize_embed_content, validate_embed_data, sanitize_content
    - EmbedCRUDMixin: create_embed, update_embed, delete_embed
    - EmbedAttachmentMixin: attach_embed_to_message, remove_embed_from_message,
      get_message_embeds, suppress_embeds, unsuppress_embeds
    - EmbedURLPreviewMixin: _scrape_url_metadata, create_url_preview, parse_url_metadata
    - EmbedManagerBase: get_embed, _row_to_embed, _get_message, _is_participant,
      _get_channel_for_conversation, _check_embed_links_permission
    """

    def __init__(
        self,
        db,
        messaging_module=None,
        servers_module=None,
        media_proxy=None,
    ):
        """
        Initialize the embed manager.

        Args:
            db: Database instance (must be connected)
            messaging_module: Messaging module for message access
            servers_module: Servers module for permission checks
            media_proxy: Optional media proxy for image caching
        """
        super().__init__(
            db,
            messaging_module=messaging_module,
            servers_module=servers_module,
            media_proxy=media_proxy,
        )
        logger.info("Embed module initialized")
