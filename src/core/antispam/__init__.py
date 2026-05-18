"""
DM Anti-Spam module - Rate limiting, pattern detection, and spam prevention for direct messages.
"""

from .detector import DMSpamDetector

__all__ = ["DMSpamDetector"]
