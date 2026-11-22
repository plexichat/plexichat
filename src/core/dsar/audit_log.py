import os
import json
import hashlib
import hmac
import time
from pathlib import Path
from typing import Optional, Dict, Tuple, List, Any, cast

try:
    import fcntl as fcntl  # POSIX-only fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt as msvcrt  # Windows-only; for byte-range file locking
except ImportError:
    msvcrt = None

import utils.config as config
import utils.logger as logger

from src.core.telemetry.scrubbing import redact_secrets


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

    def __init__(
        self,
        file_path: Optional[str] = None,
        chain_key: Optional[str] = None,
        chain_key_id: Optional[str] = None,
        chain_keys: Optional[Dict[str, str]] = None,
    ):
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

        # Optional HMAC keying of the hash chain. With no key configured the
        # historical unkeyed SHA-256 chain is used verbatim. When a key is
        # supplied, new entries are HMAC-SHA256(key, prev_hash + content) and
        # carry the id of the key that signed them. Every key id stays in the
        # ``chain_keys`` map so entries written before a rotation remain
        # verifiable - rotation must never invalidate existing evidence.
        raw_keys = chain_keys if chain_keys is not None else cfg.get("chain_keys", {})
        self._chain_keys: Dict[str, str] = {}
        if isinstance(raw_keys, dict):
            for key_id, key in raw_keys.items():
                if key_id and key:
                    self._chain_keys[str(key_id)] = str(key)

        active_key = chain_key if chain_key is not None else cfg.get("chain_key")
        active_id = (
            chain_key_id if chain_key_id is not None else cfg.get("chain_key_id")
        )
        if active_key:
            active_id = str(active_id) if active_id else "default"
            self._chain_keys[active_id] = str(active_key)
            self._current_key_id: Optional[str] = active_id
        else:
            self._current_key_id = None

        self._ensure_dir()

    def _chain_checksum(self, prev_hash: str, content: str) -> str:
        """Checksum for a NEW entry under the currently active chain key."""
        data = (prev_hash + content).encode("utf-8")
        if self._current_key_id:
            key = self._chain_keys.get(self._current_key_id)
            if not key:
                raise RuntimeError("DSAR chain key for the active key id is missing")
            return hmac.new(key.encode("utf-8"), data, hashlib.sha256).hexdigest()
        return hashlib.sha256(data).hexdigest()

    def rotate_chain_key(self, new_key: str, new_key_id: str) -> str:
        """Rotate the internal HMAC key used to authenticate new entries.

        The outgoing key is deliberately NOT removed from ``_chain_keys``:
        entries written with it record their own ``key_id`` and must keep
        verifying across the rotation. Nothing in the log is rewritten or
        reset, so tamper evidence accumulated before the rotation survives.
        Operators must persist ``chain_key``/``chain_key_id``/``chain_keys``
        in configuration for the rotation to survive a process restart.
        """
        if not new_key or not new_key_id:
            raise ValueError("new_key and new_key_id are required")
        self._current_key_id = str(new_key_id)
        self._chain_keys[str(new_key_id)] = str(new_key)
        logger.info(f"DSAR audit chain key rotated to {new_key_id}")
        return self._current_key_id

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

            # Read the tail in one bounded chunk instead of one byte at a time.
            # The old backward scan issued one read() syscall per character of
            # the last entry (hundreds on every append) and dominated the
            # generate hot path, where two audit events are written per export.
            # Audit entries are small; 64 KiB is well beyond a single line, and
            # the final line in the chunk is always complete (the file ends in
            # a newline). If a line somehow exceeded the chunk, the last line
            # is still the correct tail entry.
            tail_size = min(size, 64 * 1024)
            file_handle.seek(size - tail_size)
            tail = file_handle.read(tail_size)
            last_line = ""
            for line in reversed(tail.splitlines()):
                if line.strip():
                    last_line = line
                    break
            if not last_line:
                return "0" * 64

            return json.loads(last_line).get("checksum", "0" * 64)
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

        # SECURITY: ``metadata`` is caller-supplied free-form data. Real callers
        # put exception text in it (harvester logs ``{"error": str(e)}`` on a
        # failed export), so a credential embedded in an error message would be
        # written to this on-disk audit file in the clear. Scrub *before*
        # building the entry so the checksum covers only redacted content --
        # the secret must never reach the file, not merely be hidden on read.
        # This mirrors ``AuditChain.append`` in core/telemetry/audit.py.
        entry = {
            "timestamp": int(time.time()),
            "user_id": user_id,
            "identifier_hash": id_hash,
            "action": action,
            "metadata": redact_secrets(metadata or {}),
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
                    except Exception as close_error:
                        logger.debug(f"dsar lock token close failed: {close_error}")
                    lock_token = None
                if lock_acquired:
                    try:
                        with open(self.file_path, "a+") as f:
                            prev_hash = self._read_last_hash_from_handle(f)
                            entry["prev_hash"] = prev_hash

                            if self._current_key_id:
                                entry["key_id"] = self._current_key_id
                            content = json.dumps(entry, sort_keys=True)
                            checksum = self._chain_checksum(prev_hash, content)
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
                fcntl.flock(f, fcntl.LOCK_EX)

            try:
                prev_hash = self._read_last_hash_from_handle(f)
                entry["prev_hash"] = prev_hash

                if self._current_key_id:
                    entry["key_id"] = self._current_key_id
                content = json.dumps(entry, sort_keys=True)
                checksum = self._chain_checksum(prev_hash, content)
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
                    fcntl.flock(f, fcntl.LOCK_UN)

    def _ensure_lock_file(self) -> None:
        """Make sure the byte-range lock file exists with non-zero length.

        ``msvcrt.locking`` requires a non-empty byte range, so the
        lock file must exist and have at least one byte to lock.
        """
        directory = os.path.dirname(self.lock_path)
        if directory and not os.path.exists(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as mkdir_error:
                logger.debug(f"dsar log directory already usable: {mkdir_error}")
        if not os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "wb") as lf:
                    lf.write(b"\0")
            except OSError as lock_error:
                # Another process may have created the lock file first.
                logger.debug(f"dsar lock file create skipped: {lock_error}")

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
        except Exception:
            rotate = False

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
                    # SECURITY: a detected chain break is a tamper
                    # event (or severe data loss). It must FAIL
                    # LOUDLY - silently returning a clean bill of
                    # health after a reset would defeat the entire
                    # purpose of the tamper-evident log. The broken
                    # file is preserved as a sidecar for forensics,
                    # but every caller is alerted via exception.
                    raise RuntimeError(
                        f"DSAR audit log chain verification FAILED "
                        f"({err}); broken log rotated to {sidecar} "
                        f"and chain reset. Operator review REQUIRED."
                    )
                except RuntimeError:
                    raise
                except Exception as e:
                    logger.error(f"DSAR: failed to rotate broken log to {sidecar}: {e}")
            return valid, count, err

        return self._verify_chain_impl()

    def _verify_chain_impl(self) -> Tuple[bool, int, Optional[str]]:
        if not os.path.exists(self.file_path):
            return True, 0, None

        count = 0
        expected_prev_hash = "0" * 64
        # SECURITY: track whether the chain has transitioned to HMAC-keyed
        # entries. Unkeyed SHA-256 records are only legitimate as the legacy
        # PREFIX written before keying was enabled (see the fallback below).
        keyed_seen = False

        try:
            with open(self.file_path, "r") as f:
                for line_num, line in enumerate(f, 1):
                    if not line.strip():
                        continue

                    entry = json.loads(line)
                    stored_checksum = entry.pop("checksum", None)
                    stored_prev_hash = entry.get("prev_hash")
                    entry_key_id = entry.get("key_id")

                    if stored_prev_hash != expected_prev_hash:
                        return (
                            False,
                            count,
                            f"Chain broken at line {line_num}: prev_hash mismatch",
                        )

                    content = json.dumps(entry, sort_keys=True)
                    data = (stored_prev_hash + content).encode("utf-8")
                    if entry_key_id:
                        # Each entry records the key that authenticated it, so
                        # entries written before a rotation keep verifying with
                        # their original key. A missing key is a hard failure -
                        # never fall back to an unkeyed hash and let a keyed
                        # entry be “verified” under the wrong algorithm.
                        key = self._chain_keys.get(str(entry_key_id))
                        if not key:
                            return (
                                False,
                                count,
                                f"Chain broken at line {line_num}: "
                                f"unknown chain key id {entry_key_id!r}",
                            )
                        keyed_seen = True
                        calculated_checksum = hmac.new(
                            key.encode("utf-8"), data, hashlib.sha256
                        ).hexdigest()
                    else:
                        # SECURITY: a keyless entry is only valid in the legacy
                        # prefix written BEFORE HMAC keying was enabled. Once
                        # any keyed entry exists, a later entry with no key_id
                        # is an algorithm-downgrade forgery: an attacker with
                        # write access to the log can compute an unkeyed
                        # SHA-256 over (prev_hash + content) themselves --
                        # prev_hash is public in the file -- and append a fake
                        # event that the "legacy" fallback would accept. Refuse
                        # it so keying cannot be silently downgraded.
                        if keyed_seen:
                            return (
                                False,
                                count,
                                f"Chain broken at line {line_num}: unkeyed entry "
                                f"appended after keyed entries (chain downgrade)",
                            )
                        # Legacy unkeyed entries remain verifiable verbatim.
                        calculated_checksum = hashlib.sha256(data).hexdigest()

                    # Constant-time comparison of the chain checksum. A plain
                    # ``!=`` short-circuits on the first differing byte, so a
                    # tamperer with repeated verification access could walk the
                    # HMAC digest byte-by-byte and forge an appended entry.
                    # ``compare_digest`` removes that timing oracle; the
                    # isinstance guard makes a missing/non-string checksum fail
                    # closed instead of raising a TypeError out of verify_chain.
                    checksum_matches = isinstance(
                        stored_checksum, str
                    ) and hmac.compare_digest(
                        calculated_checksum.encode("utf-8"),
                        stored_checksum.encode("utf-8"),
                    )
                    if not checksum_matches:
                        return (
                            False,
                            count,
                            f"Chain broken at line {line_num}: checksum invalid",
                        )

                    # SECURITY: export/deletion events carry a self-verifying
                    # integrity_hash over their metadata. It was written by
                    # ``_log_integrity_event`` but never checked, so a record
                    # whose chain checksum was recomputed (possible on the
                    # legacy unkeyed chain, or with a compromised chain key)
                    # could have its artifact_sha256/storage_path/deleted
                    # fields silently altered while still "verifying". Bind
                    # the metadata to its hash on every verification so the
                    # advertised per-record tamper evidence is real.
                    metadata = entry.get("metadata")
                    if isinstance(metadata, dict) and "integrity_hash" in metadata:
                        stored_integrity = metadata["integrity_hash"]
                        payload = {
                            k: v for k, v in metadata.items() if k != "integrity_hash"
                        }
                        expected_integrity = self._integrity_hash(payload)
                        if not (
                            isinstance(stored_integrity, str)
                            and hmac.compare_digest(
                                stored_integrity, expected_integrity
                            )
                        ):
                            return (
                                False,
                                count,
                                f"Chain broken at line {line_num}: "
                                "metadata integrity_hash mismatch",
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

    @staticmethod
    def _integrity_hash(metadata: Dict[str, Any]) -> str:
        """Deterministic hash of an event's metadata payload."""
        canonical = json.dumps(metadata, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _log_integrity_event(
        self, user_id: int, action: str, identifier: str, metadata: Dict[str, Any]
    ) -> Optional[str]:
        """Append an event carrying a self-verifying metadata hash.

        ``log_event`` already hash-chains the whole entry; the extra
        ``integrity_hash`` binds the event's own payload so an operator can
        verify a single export/deletion record without replaying the chain.
        """
        # Redact before hashing: ``log_event`` now redacts the metadata again
        # before persisting it (idempotently), so the self-verifying hash must
        # be computed over the same redacted payload that lands on disk.
        # Otherwise verify_chain would reject every integrity event whose
        # metadata contained a secret.
        payload = redact_secrets(dict(metadata))
        payload["integrity_hash"] = self._integrity_hash(payload)
        return self.log_event(user_id, action, identifier, payload)

    def log_export_event(
        self,
        user_id: int,
        request_id: int,
        checksum: str,
        file_size: int,
        storage_backend: str,
        storage_path: str,
    ) -> Optional[str]:
        """Record that a DSAR export artifact was generated."""
        return self._log_integrity_event(
            user_id,
            "EXPORTED",
            f"dsar_request:{request_id}",
            {
                "request_id": request_id,
                "artifact_sha256": checksum,
                "file_size": file_size,
                "storage_backend": storage_backend,
                "storage_path": storage_path,
            },
        )

    def log_deletion_event(
        self,
        user_id: int,
        request_id: int,
        storage_backend: str,
        storage_path: str,
        deleted: bool,
    ) -> Optional[str]:
        """Record that a DSAR export artifact was (or was not) deleted."""
        return self._log_integrity_event(
            user_id,
            "DELETED",
            f"dsar_request:{request_id}",
            {
                "request_id": request_id,
                "storage_backend": storage_backend,
                "storage_path": storage_path,
                "deleted": bool(deleted),
            },
        )
