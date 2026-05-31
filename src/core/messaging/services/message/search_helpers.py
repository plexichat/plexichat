"""
Module-level search indexing helpers for the MessageService.

These are defined at module level to avoid circular import issues.
They safely check if the search module is initialized before indexing.
"""

from typing import Any, Dict, Optional

from src.core.base import SnowflakeID


def search_index_message(
    message_id: SnowflakeID,
    plaintext_content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Index a message for search with DECRYPTED plaintext content.

    Args:
        message_id: ID of the message
        plaintext_content: The DECRYPTED plaintext content (NOT encrypted)
        metadata: Additional metadata (author_id, conversation_id, etc.)
    """
    try:
        import src.core.search as search_module

        search_module.index_message(
            message_id=int(message_id),
            content=str(plaintext_content) if plaintext_content else "",
            metadata=metadata,
        )
    except (RuntimeError, AttributeError, Exception):
        pass


def search_remove_from_index(message_id: SnowflakeID) -> None:
    """
    Remove a message from the search index.

    Args:
        message_id: ID of the message to remove
    """
    try:
        import src.core.search as search_module

        search_module.remove_from_index(int(message_id))
    except (RuntimeError, AttributeError, Exception):
        pass
