"""
DeletionLog - cryptographically chained, append-only audit log for account deletions.

Provides tamper-evident hash chain so operators can detect post-hoc
modification of GDPR-relevant events.  Cross-platform OS-level
file locking with graceful no-op fallback when neither fcntl nor
msvcrt is available (CRITICAL log so the operator notices).
"""

import os
import json
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Tuple

import utils.config as config
import utils.logger as logger


# ---------------------------------------------------------------------------
# Cross-platform locking: prefer fcntl (Unix), fall back to msvcrt
# (Windows). When neither module is available (rare on bare-metal / WSL1
# without /dev/hvc0) we degrade to a cooperative no-op and CRITICAL-log
# so operators can investigate. ``dsar/audit_log.py`` is the canonical
# reference for this pattern; this module implements the same shim with a
# slimmer critical section (single-file flock instead of byte-range lock).
# ---------------------------------------------------------------------------
try:
    import fcntl  # type: ignore[import-not-found]
except ImportError:
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt  # type: ignore[import-not-found]
except ImportError:
    msvcrt = None  # type: ignore[assignment]


class _NoOpLock:
    """Lock shim used when neither fcntl nor msvcrt is importable."""

    def __enter__(self) -> "_NoOpLock":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FcntlLock:
    """fcntl-flock wrapper (POSIX)."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def __enter__(self) -> "_FcntlLock":
        if fcntl is not None:  # defensive: caller already checked
            fcntl.flock(self._fd, fcntl.LOCK_EX)  # type: ignore[attr-defined]  # fcntl is Linux-only
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]  # fcntl is Linux-only
        except Exception:
            pass


class _MsvcrtLock:
    """msvcrt byte-range locking wrapper (Windows).

    Locks the first byte of ``self._fd`` for the duration of the
    critical section. ``msvcrt.locking`` does not expose a
    flock-equivalent, so byte-0 is the canonical "this file is being
    written" signal used by other Windows processes that open the same
    audit log for append.
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def __enter__(self) -> "_MsvcrtLock":
        if msvcrt is not None:
            try:
                msvcrt.locking(self._fd, msvcrt.LK_LOCK, 1)
            except Exception as e:  # pragma: no cover -- Windows-only
                logger.warning(
                    "deletion_log: msvcrt LK_LOCK failed (%s); continuing without "
                    "cross-process serialisation",
                    e,
                )
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            if msvcrt is not None:
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
        except Exception:
            pass


def _file_lock(file_handle) -> object:
    """Return a context manager that holds an OS-level exclusive lock."""
    if fcntl is not None:
        return _FcntlLock(file_handle.fileno())
    if msvcrt is not None:
        return _MsvcrtLock(file_handle.fileno())
    logger.critical(
        "deletion_log: NO OS-level file lock available (no fcntl, no msvcrt). "
        "Concurrent writers can corrupt the hash chain. This MUST NOT be "
        "used in production."
    )
    return _NoOpLock()


class DeletionLog:
    """
    Append-only audit log of account-deletion events with a tamper-evident
    SHA-256 hash chain.  Each record carries:

    * ``prev_hash`` — the SHA-256 hex of the previous record's checksum
      (or 64 zero hex chars for the genesis block);
    * ``checksum``  — SHA-256 hex of ``prev_hash`` concatenated with the
      canonical JSON form of the other fields.

    Operators can call :meth:`verify_chain` to detect post-hoc editing,
    truncation, or replay of records.
    """

    def __init__(self, file_path: Optional[str] = None):
        cfg = config.get("authentication.account_deletion.audit_log", {})
        self.file_path = file_path or cfg.get(
            "file_path",
            str(Path.home() / ".plexichat" / "data" / "deletion_log.jsonl"),
        )
        self.hash_chain_enabled = cfg.get("hash_chain_enabled", True)
        self._ensure_dir()

    # ------------------------------------------------------------------ #
    # Path / directory helpers                                            #
    # ------------------------------------------------------------------ #
    def _ensure_dir(self) -> None:
        directory = os.path.dirname(self.file_path)
        if directory and not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to create audit log directory {directory}: {e}")

    def _rotate_broken_log(self, reason: str) -> Optional[str]:
        """Move the current log to ``.broken-<ts>`` so a fresh chain can start.

        Returns the sidecar path on success, ``None`` on failure.  Used
        when ``rotate_on_broken_chain`` is configured AND a real chain
        break is detected, so the daemon can keep functioning without
        dropping GDPR-impacting entries silently.
        """
        if not os.path.exists(self.file_path):
            return None
        ts = int(time.time())
        sidecar = f"{self.file_path}.broken-{ts}"
        try:
            os.rename(self.file_path, sidecar)
            logger.critical(
                f"deletion_log: chain verification failed ({reason}); rotated "
                f"broken log file to {sidecar}. Future writes start a new "
                "chain with prev_hash='0'*64."
            )
            return sidecar
        except Exception as e:
            logger.error(f"deletion_log: failed to rotate broken log to {sidecar}: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Hash-chain write path                                               #
    # ------------------------------------------------------------------ #
    def _read_last_hash_from_handle(self, file_handle) -> str:
        """Read the prev_hash from the SAME locked file handle.

        SECURITY: the previous implementation opened a fresh file
        handle to read prev_hash, which is NOT serialised by the
        fcntl/msvcrt lock held by the writer. Two concurrent writers
        could each observe the same last entry and produce a
        colliding hash chain (a "fork" — the audit log becomes
        inconsistent). Reading on the SAME handle inside the lock
        closes the race so the writer's atomic block-write of
        (read, hash, write, fsync) is one critical section bounded
        by the OS-level lock.

        Returns the canonical 64 zero hex chars for the genesis
        position (empty file or the last line is unreadable).
        """
        try:
            file_handle.seek(0, os.SEEK_END)
            size = file_handle.tell()
            if size == 0:
                return "0" * 64
            pos = size
            buffer = b""
            while pos > 0:
                pos -= 1
                file_handle.seek(pos)
                char = file_handle.read(1)
                if char == b"\n" and buffer:
                    break
                buffer = char + buffer

            if not buffer:
                return "0" * 64

            last_line = json.loads(buffer.decode("utf-8"))
            return last_line.get("checksum", "0" * 64)
        except Exception as e:
            logger.error(f"Error reading last hash from audit log (handle): {e}")
            return "0" * 64

    def log_event(
        self,
        user_id: int,
        action: str,
        identifier: str,
        metadata: Optional[Dict] = None,
    ) -> Optional[str]:
        """Append a deletion event to the chained log.

        Returns the checksum hex string for the appended record, or
        ``None`` on failure.  The chain is protected by an OS-level
        file lock acquired against the SAME file handle used for
        prev-hash read + write + fsync, so two plexichat processes
        cannot fork the chain.
        """
        id_hash = hashlib.sha256(identifier.encode()).hexdigest()

        entry: Dict[str, object] = {
            "timestamp": int(time.time()),
            "user_id": user_id,
            "identifier_hash": id_hash,
            "action": action,
            "metadata": metadata or {},
        }

        try:
            with open(self.file_path, "a+") as f:
                with _file_lock(f):  # type: ignore[no-untyped-def]  # _file_lock typed as `object`; runtime is a contextmanager
                    prev_hash = self._read_last_hash_from_handle(f)
                    entry["prev_hash"] = prev_hash

                    content = json.dumps(entry, sort_keys=True)
                    checksum = hashlib.sha256(
                        (prev_hash + content).encode()
                    ).hexdigest()
                    entry["checksum"] = checksum

                    f.write(json.dumps(entry) + "\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        # fsync may fail on some FUSE/overlay mounts.
                        # Continue — the OS-level lock has already
                        # serialised the critical section so durability
                        # is bounded by the device's flush policy.
                        pass

                    logger.info(
                        f"deletion_log: {action} for user {user_id} "
                        f"(checksum {checksum[:8]}…)"
                    )
                    return checksum
        except Exception as e:
            logger.error(f"Failed to write to deletion audit log: {e}")
            return None

    # ------------------------------------------------------------------ #
    # Hash-chain verification                                             #
    # ------------------------------------------------------------------ #
    def verify_chain(self) -> Tuple[bool, int, Optional[str]]:
        """Verify the integrity of the entire hash chain.

        Returns ``(is_valid, record_count, error_message)``.

        Edge cases that ARE considered valid:

        * file does not exist (``is_valid=True, count=0``);
        * file exists but is empty / whitespace-only (``is_valid=True, count=0``);
        * file contains exactly one record whose ``prev_hash`` is the
          canonical genesis (``is_valid=True, count=1``).

        Anything else where the chain doesn't validate triggers
        ``is_valid=False``.  When ``rotate_on_broken_chain`` is set in
        the audit_log config block, the broken file is rotated aside
        so subsequent writes can begin a fresh chain without operator
        intervention — the verification result is then ``is_valid=True
        (after rotate), count=0``.
        """
        if not os.path.exists(self.file_path):
            return True, 0, None

        try:
            rotate = bool(
                config.get(
                    "authentication.account_deletion.audit_log",
                    {},
                ).get("rotate_on_broken_chain", False)
            )
        except Exception:
            rotate = False

        valid, count, err = self._verify_chain_impl()
        if not valid and rotate and err is not None:
            sidecar = self._rotate_broken_log(err)
            if sidecar is not None:
                return True, 0, None
        return valid, count, err

    def _verify_chain_impl(self) -> Tuple[bool, int, Optional[str]]:
        if not os.path.exists(self.file_path):
            return True, 0, None

        count = 0
        expected_prev_hash = "0" * 64

        try:
            with open(self.file_path, "r") as f:
                for line_num, line in enumerate(f, 1):
                    if not line.strip():
                        continue

                    entry = json.loads(line)
                    stored_checksum = entry.pop("checksum", None)
                    stored_prev_hash = entry.get("prev_hash")

                    if stored_prev_hash != expected_prev_hash:
                        return (
                            False,
                            count,
                            f"Chain broken at line {line_num}: prev_hash mismatch",
                        )

                    content = json.dumps(entry, sort_keys=True)
                    calculated_checksum = hashlib.sha256(
                        (stored_prev_hash + content).encode()
                    ).hexdigest()

                    if calculated_checksum != stored_checksum:
                        return (
                            False,
                            count,
                            f"Chain broken at line {line_num}: checksum invalid",
                        )

                    expected_prev_hash = stored_checksum
                    count += 1

            return True, count, None
        except Exception as e:
            return False, count, str(e)

    def get_scheduled_deletions(self) -> Dict[int, Dict]:
        """Return the current set of ``user_id -> SCHEDULED entry``.

        Walks the log forward so that transitions SCHEDULED → CANCELLED /
        SCHEDULED → PURGED clean up the in-memory map.  The mapper is
        used by :class:`AccountReaper` to reconcile the log against the
        DB during a rollback-protection scan.
        """
        state: Dict[int, Dict] = {}
        if not os.path.exists(self.file_path):
            return state

        try:
            with open(self.file_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    uid = entry["user_id"]
                    action = entry["action"]

                    if action == "SCHEDULED":
                        state[uid] = entry
                    elif action in ("CANCELLED", "PURGED"):
                        if uid in state:
                            del state[uid]
            return state
        except Exception as e:
            logger.error(f"Failed to read scheduled deletions from log: {e}")
            return state
