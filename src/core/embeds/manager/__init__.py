"""
Embed manager package - Mixin-based architecture.

Re-exports EmbedManager from composer for backwards compatibility.
"""

from src.core.embeds.manager.composer import EmbedManager

__all__ = ["EmbedManager"]
