"""
Cross-platform file locking helpers.

Provides cooperative file locking using fcntl (Unix) or msvcrt (Windows).

Notes:
- The caller MUST open the lock file in BINARY mode ("ab"/"rb+") rather than
  text mode — ``msvcrt.locking`` operates on raw bytes and Python's text-mode
  translation can shift byte offsets, producing silent failures when other
  processes open the same file.
- The caller MUST NOT truncate the file between acquire and release -
  opening in "w" mode drops existing content and creates a window where a
  second process can recreate the lock file from scratch and acquire it
  concurrently. Use "ab" (append+read) instead.
- On Windows, ``msvcrt.locking`` only supports LK_LOCK (blocking forever)
  and LK_NBLCK (try once). There is no shared-vs-exclusive distinction.
  We always use LK_NBLCK so a contended process cannot wedge the server,
  and rely on the caller's retry-with-backoff to provide bounded blocking.
"""

import sys


def acquire_file_lock(
    lock_file, exclusive: bool = True, blocking: bool = False
) -> bool:
    """Acquire a cooperative cross-platform file lock.

    Args:
        lock_file: An open file-like object that supports ``.fileno()``.
            Must be opened in binary mode.
        exclusive: POSIX only. When True (default), take an exclusive lock;
            when False, take a shared lock. For Windows ``msvcrt`` the
            parameter is accepted for API symmetry but ignored.
        blocking: POSIX only. When True, block until the lock is acquired.
            Default is False (try once). Windows is always non-blocking
            because msvcrt does not support unbounded blocking.

    Returns:
        True if the lock was acquired, False if the OS reported contention.
    """
    if sys.platform == "win32":
        import msvcrt

        try:
            # Windows has no shared/exclusive distinction - always non-blocking
            # to allow the caller to retry/timeout and avoid wedges.
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except (IOError, OSError):
            return False
    else:
        import fcntl

        try:
            op = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            if not blocking:
                op |= fcntl.LOCK_NB
            fcntl.flock(lock_file.fileno(), op)
            return True
        except (IOError, OSError):
            return False


def release_file_lock(lock_file) -> None:
    """Release a cooperative cross-platform file lock."""
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
