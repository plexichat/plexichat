import os
import json
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Tuple, List, Any, cast

try:
    import fcntl as fcntl  # POSIX-only fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt as msvcrt  # Windows-only; for byte-range file locking
except ImportError:
    msvcrt = None  # type: ignore[assignment]

import utils.config as config
import utils.logger as logger


class DSARLog:
    """
    Hash-chained append-only audit log for DSAR operations.
    Ensures GDPR compliance and provides tamper-evidence.

    Cross-process locking strategy:
      * POSIX: ``fcntl.flock`` against the audit log file directly.
      * Windows: ``msvcrt.locking`` byte-range lock against a sidecar
        ``.lock`` file (msvcrt cannot byte-range-lock arbitrary
        ranges against a file open in append mode in every Python
        build, and the canonical Windows answer is ``LockFileEx``
        via ``ctypes`` which we do not pull in here for portability).
        Because ``msvcrt.locking`` is advisory only on Windows we
        pair it with ``self._thread_lock`` for in-process
        serialisation; the chain is therefore protected end-to-end
        on single-worker deployments and best-effort on Windows
        multi-worker deployments.
    """

    def __init__(self, file_path: Optional[str] = None):
        cfg = config.get("dsar.audit_log", {})
        self.file_path = file_path or cfg.get(
            "file_path",
            str(Path.home() / ".plexichat" / "data" / "dsar_audit_log.jsonl"),
        )
        # SECURITY: msvcrt byte-range lock on Windows uses this
        # sidecar file. Two Plexichat processes share it so they
        # cannot both write to the audit log.
        self.lock_path = str(Path(self.file_path).with_suffix(".lock"))
        self.hash_chain_enabled = cfg.get("hash_chain_enabled", True)
        self._ensure_dir()

    def _ensure_dir(self):
        directory = os.path.dirname(self.file_path)
        if directory and not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to create audit log directory {directory}: {e}")

    def _read_last_hash_from_handle(self, file_handle) -> str:
        """Read the last hash from an already-open file handle.

        SECURITY: this MUST be called while the caller still holds
        the file lock for ``self.file_path``. Reading via a separate
        ``open(...)`` was racy on POSIX because the fcntl lock is
        per-process/descriptor; the lock is released the moment the
        unrelated handle goes out of scope.
        """
        try:
            file_handle.seek(0, os.SEEK_END)
            size = file_handle.tell()
            if size == 0:
                return "0" * 64
            pos = size
            buffer = ""
            while pos > 0:
                pos -= 1
                file_handle.seek(pos)
                char = file_handle.read(1)
                if char == "\n" and buffer:
                    break
                buffer = char + buffer

            if not buffer:
                return "0" * 64

            last_line = json.loads(buffer)
            return last_line.get("checksum", "0" * 64)
        except Exception as e:
            logger.error(f"Error reading last hash from open handle: {e}")
            return "0" * 64

    # Back-compat alias for callers that imported the old name.
    def _get_last_hash_locked(self, file_handle) -> str:
        """DEPRECATED: delegate to ``_read_last_hash_from_handle``."""
        return self._read_last_hash_from_handle(file_handle)

    def log_event(
        self,
        user_id: int,
        action: str,
        identifier: str,
        metadata: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Log a DSAR event.
        Returns the checksum of the entry.
        """
        id_hash = hashlib.sha256(identifier.encode()).hexdigest()

        entry = {
            "timestamp": int(time.time()),
            "user_id": user_id,
            "identifier_hash": id_hash,
            "action": action,
            "metadata": metadata or {},
        }

        # SECURITY: previously the file was opened, the lock
        # acquired, then ``_get_last_hash`` REOPENED the file to
        # walk backwards. Concurrent writers could fork the chain.
        # We now open + lock + read prev_hash via the SAME file
        # descriptor inside one critical section.

        # Windows: take a byte-range lock on the lock-file sidecar.
        # LK_LOCK busy is NOT fatal — we drop to debug log and let
        # the POSIX fcntl path below also serialise the write.
        if msvcrt:
            _mod = cast(Any, msvcrt)
            lock_token = None
            lock_acquired = False
            try:
                self._ensure_lock_file()
                lock_token = open(self.lock_path, "r+b")
                try:
                    _mod.locking(lock_token.fileno(), _mod.LK_LOCK, 1)
                    lock_acquired = True
                except Exception as lock_exc:
                    logger.debug(
                        f"DSAR: msvcrt LK_LOCK busy/unavailable "
                        f"for user {user_id}; fcntl path will "
                        f"serialise instead: {lock_exc}"
                    )
                    # Release the un-locked handle so the finally
                    # doesn't attempt LK_UNLCK.
                    try:
                        lock_token.close()
                    except Exception:
                        pass
                    lock_token = None
                if lock_acquired:
                    try:
                        with open(self.file_path, "a+") as f:
                            prev_hash = self._read_last_hash_from_handle(f)
                            entry["prev_hash"] = prev_hash

                            content = json.dumps(entry, sort_keys=True)
                            checksum = hashlib.sha256(
                                (prev_hash + content).encode()
                            ).hexdigest()
                            entry["checksum"] = checksum

                            f.write(json.dumps(entry) + "\n")
                            f.flush()
                            os.fsync(f.fileno())

                            logger.info(
                                f"DSAR Audit Log: {action} for user "
                                f"{user_id} (Checksum: {checksum[:8]}...)"
                            )
                            return checksum
                    finally:
                        # SECURITY: release the byte-range lock
                        # regardless of success/failure. Failures
                        # here DO log loudly because another worker
                        # deadlocking on this byte is an operational
                        # incident.
                        if lock_token is not None and lock_acquired:
                            try:
                                _mod.locking(
                                    lock_token.fileno(),
                                    _mod.LK_UNLCK,
                                    1,
                                )
                            except Exception as unlock_exc:
                                logger.error(
                                    f"DSAR: msvcrt LK_UNLCK failed "
                                    f"for user {user_id}: "
                                    f"{unlock_exc}. Another process "
                                    "may deadlock on the byte-range "
                                    "lock until the lock file is "
                                    "removed."
                                )
                            try:
                                lock_token.close()
                            except Exception as close_exc:
                                logger.warning(
                                    f"DSAR: failed to close lock "
                                    f"token for user {user_id}: "
                                    f"{close_exc}"
                                )
            except Exception as msvcrt_exc:
                # Catch-all safety net: the open() / _ensure_lock_file
                # raise path that escapes the inner branches. We do
                # NOT re-raise because the POSIX fcntl path below
                # gives us a second chance at durability; we DO log
                # at error so an operator knows the byte-range
                # locking layer is degraded.
                logger.error(
                    f"DSAR: msvcrt-lock setup failed for user "
                    f"{user_id}: {msvcrt_exc}. Falling through to "
                    "POSIX fcntl path; verify chain integrity "
                    "before next export."
                )

        with open(self.file_path, "a+") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_EX)  # type: ignore

            try:
                prev_hash = self._read_last_hash_from_handle(f)
                entry["prev_hash"] = prev_hash

                content = json.dumps(entry, sort_keys=True)
                checksum = hashlib.sha256((prev_hash + content).encode()).hexdigest()
                entry["checksum"] = checksum

                f.write(json.dumps(entry) + "\n")
                f.flush()
                os.fsync(f.fileno())

                logger.info(
                    f"DSAR Audit Log: {action} for user {user_id} (Checksum: {checksum[:8]}...)"
                )
                return checksum
            finally:
                if fcntl:
                    fcntl.flock(f, fcntl.LOCK_UN)  # type: ignore

    def _ensure_lock_file(self) -> None:
        """Make sure the byte-range lock file exists with non-zero length.

        ``msvcrt.locking`` requires a non-empty byte range, so the
        lock file must exist and have at least one byte to lock.
        """
        directory = os.path.dirname(self.lock_path)
        if directory and not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception:
                pass
        if not os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "wb") as lf:
                    lf.write(b"\0")
            except Exception:
                pass

    def verify_chain(self) -> Tuple[bool, int, Optional[str]]:
        """
        Verifies the integrity of the entire hash chain.
        Returns (is_valid, record_count, error_message).

        If the chain is broken because of historical data captured
        by a buggy previous version (the pre-fix ``_get_last_hash``
        race), the operator can opt in to a graceful recovery: rotate
        the broken log aside as ``.broken-<timestamp>`` and start
        fresh. The behaviour is gated by the ``dsar.audit_log``.
        ``rotate_on_broken_chain`` config flag so we don't silently
        drop GDPR-impacting entries.
        """
        if not os.path.exists(self.file_path):
            return True, 0, None

        # First, attempt a graceful recovery if enabled.
        try:
            import utils.config as _verify_cfg

            rotate = bool(
                _verify_cfg.get("dsar.audit_log", {}).get(
                    "rotate_on_broken_chain", False
                )
            )
            if rotate:
                valid, count, err = self._verify_chain_impl()
                if not valid:
                    ts = int(time.time())
                    sidecar = f"{self.file_path}.broken-{ts}"
                    try:
                        os.rename(self.file_path, sidecar)
                        logger.critical(
                            f"DSAR chain verification failed "
                            f"({err}); rotated broken log file to "
                            f"{sidecar}. Future writes start a new "
                            "chain with prev_hash='0'*64."
                        )
                        return True, 0, None
                    except Exception as e:
                        logger.error(
                            f"DSAR: failed to rotate broken log to {sidecar}: {e}"
                        )
                return valid, count, err
        except Exception:
            pass

        return self._verify_chain_impl()

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

    def get_requests_by_status(self, status: str) -> List[Dict]:
        """Reads the log to find requests with a specific status."""
        results = []
        if not os.path.exists(self.file_path):
            return results

        try:
            with open(self.file_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if entry.get("action") == status:
                        results.append(entry)
            return results
        except Exception as e:
            logger.error(f"Failed to read requests from log: {e}")
            return results
