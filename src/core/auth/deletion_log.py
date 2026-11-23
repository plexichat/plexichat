"""
DeletionLog - cryptographically chained, append-only audit log for account deletions.

Provides tamper-evident hash chain so operators can detect post-hoc
modification of GDPR-relevant events.  Cross-platform OS-level
file locking with graceful no-op fallback when neither fcntl nor
msvcrt is available (CRITICAL log so the operator notices).

Integrity model
---------------
Every checksum is an **HMAC-SHA256** over ``prev_hash + canonical(entry)``
under a per-chain key, and every record names the ``key_id`` that produced it.
An unkeyed SHA-256 chain is only corruption detection: anyone who can rewrite
the JSONL file can edit a row and recompute every downstream ``prev_hash`` and
``checksum`` from public data, and verification still passes.  Binding each row
to a secret the writer cannot read makes that rewrite detectable.

The key is either operator-supplied (``chain_key``/``chain_keys`` config or
constructor arguments) or an auto-generated ``0600`` sidecar next to the log
(mirroring ``src.core.telemetry.audit``).  Verification is constant-time and
fails closed on a missing/unknown key id, an unkeyed row, or a missing key
sidecar for a non-empty chain.  Retired keys stay in the map across rotation so
evidence written with an older key keeps verifying.
"""

import os
import json
import hmac
import secrets
import hashlib
import time
from pathlib import Path
from typing import ContextManager, Optional, Dict, Tuple, Any, cast

import utils.config as config
import utils.logger as logger


_SHA256_HEX_LEN = 64
_LOCAL_KEY_ID = "local"
_DEFAULT_KEY_ID = "default"
_MAX_KEY_CHARS = 256


def _constant_time_hex_equal(left: Any, right: Any) -> bool:
    """Compare two SHA-256 hex digests without leaking their prefix via timing.

    Both the audit-log chain links (``prev_hash``) and the per-record
    ``checksum`` are integrity values. A byte-by-byte ``==``/``!=`` short
    circuits on the first differing character, giving an attacker who can
    submit crafted records a length/prefix oracle. :func:`hmac.compare_digest`
    keeps the work proportional to the digest length instead.

    The helper fails closed: missing values, non-strings and anything that
    is not a full 64-character hex digest are rejected without an exception
    so a truncated or tampered record simply breaks the chain.
    """
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if len(left) != _SHA256_HEX_LEN or len(right) != _SHA256_HEX_LEN:
        return False
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


# ---------------------------------------------------------------------------
# Cross-platform locking: prefer fcntl (Unix), fall back to msvcrt
# (Windows). When neither module is available (rare on bare-metal / WSL1
# without /dev/hvc0) we degrade to a cooperative no-op and CRITICAL-log
# so operators can investigate. ``dsar/audit_log.py`` is the canonical
# reference for this pattern; this module implements the same shim with a
# slimmer critical section (single-file flock instead of byte-range lock).
# ---------------------------------------------------------------------------
try:
    import fcntl
except ImportError:
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None


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
            fcntl.flock(self._fd, fcntl.LOCK_EX)  # fcntl is Linux-only
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)  # fcntl is Linux-only
        except OSError as unlock_error:
            # Unlocking a descriptor the OS already closed is harmless; raising
            # here would replace the exception unwinding through this manager.
            logger.debug(f"deletion_log: unlock failed: {unlock_error}")


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
        self._mod = cast(Any, msvcrt) if msvcrt is not None else None

    def __enter__(self) -> "_MsvcrtLock":
        if self._mod is not None:
            try:
                self._mod.locking(self._fd, self._mod.LK_LOCK, 1)
            except Exception as e:  # pragma: no cover -- Windows-only
                logger.warning(
                    "deletion_log: msvcrt LK_LOCK failed (%s); continuing without "
                    "cross-process serialisation",
                    e,
                )
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            if self._mod is not None:
                self._mod.locking(self._fd, self._mod.LK_UNLCK, 1)
        except OSError as unlock_error:
            logger.debug(f"deletion_log: msvcrt unlock failed: {unlock_error}")


def _file_lock(file_handle) -> ContextManager[object]:
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
    HMAC-SHA256 hash chain.  Each record carries:

    * ``prev_hash`` — the previous record's checksum (or 64 zero hex chars
      for the genesis block);
    * ``key_id``    — the id of the chain key that authenticated the record;
    * ``checksum``  — HMAC-SHA256(key, ``prev_hash`` + canonical JSON of the
      other fields).

    Operators can call :meth:`verify_chain` to detect post-hoc editing,
    truncation, or replay of records.  Verification fails closed if a row
    is unkeyed, names an unknown key, or the chain key is missing.
    """

    def __init__(
        self,
        file_path: Optional[str] = None,
        chain_key: Optional[str] = None,
        chain_key_id: Optional[str] = None,
        chain_keys: Optional[Dict[str, str]] = None,
    ):
        cfg = self._config()
        self.file_path = file_path or cfg.get(
            "file_path",
            str(Path.home() / ".plexichat" / "data" / "deletion_log.jsonl"),
        )
        self.hash_chain_enabled = cfg.get("hash_chain_enabled", True)

        # SECURITY: resolved before any read/write so a missing key can be
        # reported (not silently minted) when the chain already holds evidence.
        self._key_error: Optional[str] = None
        self._chain_keys, self._current_key_id = self._resolve_keys(
            chain_key, chain_key_id, chain_keys, cfg
        )
        self._ensure_dir()

    # ------------------------------------------------------------------ #
    # Key management                                                      #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _config() -> Dict[str, Any]:
        """Return the account-deletion audit config section (empty if absent)."""
        try:
            cfg = config.get("authentication.account_deletion.audit_log", {})
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            return {}

    def _local_key_path(self) -> Path:
        """Path of the auto-generated key sidecar (never the log itself)."""
        return Path(str(self.file_path) + ".key")

    def _chain_has_data(self) -> bool:
        """Bounded check for existing evidence before minting a fresh key."""
        try:
            if not os.path.exists(self.file_path):
                return False
            with open(self.file_path, "rb") as handle:
                return bool(handle.read(8192).strip())
        except OSError:
            return True

    def _load_local_key(self) -> Tuple[str, str]:
        """Load (or first create) the restricted local HMAC key sidecar."""
        key_path = self._local_key_path()
        try:
            raw = key_path.read_bytes()
            text = raw.decode("ascii").strip()
            if not text or len(text) > _MAX_KEY_CHARS:
                raise ValueError("key sidecar is invalid")
            return _LOCAL_KEY_ID, text
        except FileNotFoundError:
            # Fail closed: a chain that already holds evidence must never
            # silently mint a new key, which would orphan (and effectively
            # reset verification of) every existing row. Defer the failure to
            # verification/append so ``verify_chain`` keeps its return
            # contract. The one exception is a chain written before the keyed
            # chain existed: no key can authenticate those rows, so it is
            # preserved aside once instead of blocking every append forever.
            if self._chain_has_data():
                if not (self._chain_is_prekeying() and self._adopt_prekeying_chain()):
                    self._key_error = "deletion log chain key sidecar is missing for a non-empty chain"
                    return "", ""
            key = secrets.token_hex(32)
            try:
                fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return self._load_local_key()  # another process won the race
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(key + "\n")
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            return _LOCAL_KEY_ID, key

    def _chain_is_prekeying(self) -> bool:
        """True when every existing row predates per-record chain keys.

        The keyed HMAC chain landed after deployments already held a plain
        SHA-256 chain. Those legacy rows carry no ``key_id``, so no key can
        authenticate them: leaving them in place would make every future
        append fail closed forever, and deletions would keep succeeding while
        their audit rows were dropped. A row that names a key, or a file we
        cannot parse, is never legacy -- that is a real missing-key or tamper
        signal and must stay fail-closed.
        """
        seen = False
        try:
            with open(self.file_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if not isinstance(entry, dict):
                        return False
                    if entry.get("key_id"):
                        return False
                    seen = True
        except (OSError, ValueError):
            return False
        return seen

    def _adopt_prekeying_chain(self) -> bool:
        """Preserve a pre-keying chain aside so a keyed chain can start.

        The file is renamed, never rewritten or truncated: an operator keeps
        the old rows, and the new keyed chain records every deletion from here
        on. Returns ``False`` (so the caller stays fail-closed) when the file
        cannot be moved.
        """
        preserved = f"{self.file_path}.pre-key-{int(time.time())}"
        try:
            os.rename(self.file_path, preserved)
        except FileNotFoundError:
            return True  # another worker migrated it first
        except OSError as e:
            logger.error(f"deletion_log: could not preserve pre-keying chain: {e}")
            return False
        logger.critical(
            f"deletion_log: pre-keying chain preserved at {preserved}; "
            "starting a keyed chain so deletion events are audited again"
        )
        return True

    def _resolve_keys(
        self,
        chain_key: Optional[str],
        chain_key_id: Optional[str],
        chain_keys: Optional[Dict[str, str]],
        cfg: Dict[str, Any],
    ) -> Tuple[Dict[str, str], str]:
        """Resolve configured HMAC keys and the id to use for new rows."""
        raw = chain_keys if chain_keys is not None else cfg.get("chain_keys", {})
        keys: Dict[str, str] = {}
        if isinstance(raw, dict):
            for key_id, key in raw.items():
                if key_id and key:
                    keys[str(key_id)] = str(key)[:_MAX_KEY_CHARS]

        active = chain_key if chain_key is not None else cfg.get("chain_key")
        active_id = (
            chain_key_id if chain_key_id is not None else cfg.get("chain_key_id")
        )
        if active:
            key_id = str(active_id) if active_id else _DEFAULT_KEY_ID
            keys[key_id] = str(active)[:_MAX_KEY_CHARS]
            return keys, key_id

        key_id, key = self._load_local_key()
        keys[key_id] = key
        return keys, key_id

    def _chain_checksum(self, prev_hash: str, content: str, key_id: str) -> str:
        """HMAC-SHA256 over ``prev_hash + content`` under the named chain key."""
        key = self._chain_keys.get(key_id)
        if not key:
            raise RuntimeError(f"deletion_log: chain key {key_id!r} is unavailable")
        data = (prev_hash + content).encode("utf-8")
        return hmac.new(key.encode("utf-8"), data, hashlib.sha256).hexdigest()

    def rotate_chain_key(self, new_key: str, new_key_id: str) -> str:
        """Rotate the key used for NEW rows, keeping retired keys for reads.

        The outgoing key is deliberately NOT removed from ``_chain_keys``:
        rows record their own ``key_id`` and must keep verifying across the
        rotation. Nothing in the log is rewritten or reset, so tamper evidence
        accumulated before the rotation survives. Operators must persist
        ``chain_key``/``chain_key_id``/``chain_keys`` in configuration for the
        rotation to survive a process restart.
        """
        if not new_key or not new_key_id:
            raise ValueError("new_key and new_key_id are required")
        self._current_key_id = str(new_key_id)
        self._chain_keys[str(new_key_id)] = str(new_key)
        logger.info(f"deletion_log: chain key rotated to {new_key_id}")
        return self._current_key_id

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
        cannot fork the chain.  Fails closed if the chain key is missing.
        """
        if self._key_error:
            logger.error(f"deletion_log: refusing append: {self._key_error}")
            return None

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
                with _file_lock(
                    f
                ):  # _file_lock typed as `object`; runtime is a contextmanager
                    prev_hash = self._read_last_hash_from_handle(f)
                    entry["prev_hash"] = prev_hash

                    # The key id names the secret that authenticates this row.
                    # It is public metadata: swapping it for another known id
                    # still fails the HMAC check under that different key.
                    entry["key_id"] = self._current_key_id
                    content = json.dumps(entry, sort_keys=True)
                    checksum = self._chain_checksum(
                        prev_hash, content, self._current_key_id
                    )
                    entry["checksum"] = checksum

                    f.write(json.dumps(entry) + "\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError as fsync_error:
                        # fsync may fail on some FUSE/overlay mounts.
                        # Continue — the OS-level lock has already
                        # serialised the critical section so durability
                        # is bounded by the device's flush policy.
                        logger.debug(f"deletion_log: fsync skipped: {fsync_error}")

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
        (after rotate), count=0``.  A missing key for an already-populated
        chain is NOT rotated away: it fails closed, because a key-less chain
        cannot be verified and rotating would discard the only evidence.
        """
        if self._key_error:
            return False, 0, self._key_error

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
        if self._key_error:
            return False, 0, self._key_error
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
                    entry_key_id = entry.get("key_id")

                    if not _constant_time_hex_equal(
                        stored_prev_hash, expected_prev_hash
                    ):
                        return (
                            False,
                            count,
                            f"Chain broken at line {line_num}: prev_hash mismatch",
                        )

                    # Every row must name the key that authenticated it and
                    # that key must be one we actually hold. A row with no
                    # key_id would be verifiable with public data alone (plain
                    # SHA-256), so accepting one would let a writer downgrade
                    # keying and rewrite history. Both checks are plain dict
                    # lookups — no extra timing side channel.
                    if not isinstance(entry_key_id, str) or not entry_key_id:
                        return (
                            False,
                            count,
                            f"Chain broken at line {line_num}: missing chain key id",
                        )
                    if entry_key_id not in self._chain_keys:
                        return (
                            False,
                            count,
                            f"Chain broken at line {line_num}: "
                            f"unknown chain key id {entry_key_id!r}",
                        )

                    content = json.dumps(entry, sort_keys=True)
                    calculated_checksum = self._chain_checksum(
                        stored_prev_hash, content, entry_key_id
                    )

                    # Constant-time: compare_digest removes the prefix oracle
                    # a short-circuiting ``!=`` would give a forger. The helper
                    # also rejects missing/non-string/truncated digests.
                    if not _constant_time_hex_equal(
                        calculated_checksum, stored_checksum
                    ):
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
                    elif action == "DELAYED":
                        # An admin moved the deadline: refresh the stored
                        # SCHEDULED entry's metadata so rollback re-freezes
                        # honor the NEW deletion_at instead of resurrecting
                        # the stale pre-delay deadline.
                        if uid in state:
                            try:
                                new_meta = entry.get("metadata") or {}
                            except AttributeError:
                                new_meta = {}
                            if isinstance(new_meta, dict) and "deletion_at" in new_meta:
                                stored_meta = state[uid].setdefault("metadata", {})
                                if isinstance(stored_meta, dict):
                                    stored_meta["deletion_at"] = new_meta["deletion_at"]
                    elif action in ("CANCELLED", "PURGED", "FORCE_PURGED"):
                        if uid in state:
                            del state[uid]
            return state
        except Exception as e:
            logger.error(f"Failed to read scheduled deletions from log: {e}")
            return state
