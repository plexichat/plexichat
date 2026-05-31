"""
Cross-platform file locking helpers.

Provides cooperative file locking using fcntl (Unix) or msvcrt (Windows).
"""

import sys


def acquire_file_lock(lock_file, exclusive: bool = True) -> bool:
    """Cross-platform file locking."""
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_NBLCK if not exclusive else msvcrt.LK_LOCK,
                1,
            )
            return True
        except (IOError, OSError):
            return False
    else:
        import fcntl

        try:
            fcntl.flock(
                lock_file.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            )
            return True
        except (IOError, OSError):
            return False


def release_file_lock(lock_file) -> None:
    """Cross-platform file unlock."""
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except (IOError, OSError):
            pass
    else:
        import fcntl

        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except (IOError, OSError):
            pass
