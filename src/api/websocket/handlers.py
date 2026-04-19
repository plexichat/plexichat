"""
Opcode handlers - Handle incoming gateway messages.

This module now delegates to specialized handlers in the handlers/ subdirectory.
"""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    pass

# Import the modular OpcodeHandler from the handlers subdirectory
from .handlers import OpcodeHandler

__all__ = ["OpcodeHandler"]
