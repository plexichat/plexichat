"""
DM Anti-Spam module - Rate limiting, pattern detection, and spam prevention for direct messages.
"""

from typing import Optional

from .detector import DMSpamDetector

__all__ = ["DMSpamDetector", "setup", "get_dm_spam_detector", "reset_detector"]

_detector: Optional[DMSpamDetector] = None


def setup(db, auth_module=None) -> None:
    """Initialize the DM anti-spam detector (called during server bootstrap)."""
    global _detector
    _detector = DMSpamDetector(db, auth_module)


def get_dm_spam_detector() -> Optional[DMSpamDetector]:
    """Return the wired detector, or lazily build one from the API database."""
    if _detector is not None:
        return _detector
    try:
        import src.api as api

        db = api.get_database()
    except Exception:
        return None
    if db is None:
        return None
    return DMSpamDetector(db)


def reset_detector() -> None:
    """Drop the wired singleton and process-local counters (test support)."""
    global _detector
    _detector = None
    from .detector import reset_local_windows

    reset_local_windows()
