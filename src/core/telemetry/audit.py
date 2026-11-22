"""Tamper-evident, append-only audit event store.

The response-time telemetry in :mod:`src.core.telemetry` lives in the same
observability namespace as the privileged-action audit trail.  This module is
the hardened storage layer for that trail.

Design guarantees
-----------------
* **Append-only** -- the audit log file is only ever opened in append mode and
  the public API exposes no update/delete/truncate method.  Every record gets a
  monotonic ``seq``, so a missing or reordered row is detectable.
* **Hash chained** -- each record commits to the previous record's checksum
  (``prev``) and to its own canonical JSON body (``checksum``).  Modifying any
  field breaks that record's checksum; deleting a middle row breaks the next
  row's ``prev``/``seq``.
* **Keyed (HMAC-SHA256)** -- every checksum is an HMAC over
  ``prev + canonical(entry)``, and each record names the key id that produced
  it.  This is the part that makes the chain *tamper-evidence* rather than mere
  corruption detection: with a plain, unkeyed SHA-256 an actor who can rewrite
  the JSONL file and its head sidecar can edit any row, recompute every
  downstream ``prev``/``checksum`` from public data, and pass verification.
  An operator-supplied ``chain_key`` (preferred) or an auto-generated,
  ``0600``-mode local key sidecar supplies the secret.  Verification is
  constant-time and fails closed on a missing/unknown key or an unkeyed row in
  a keyed chain (downgrade).  A DB-only attacker cannot read either key.
* **Head commitment** -- a small sidecar file records the row count and the
  last checksum.  It is updated atomically after each append, so *tail*
  truncation (deleting the newest rows) is detected too -- a plain chain
  cannot see that, because nothing follows the deleted rows.
* **Bounded** -- actions, actors, payloads, sequence numbers and reads are all
  capped so a privileged caller cannot turn the audit trail into an unbounded
  allocation.

``verify_chain()`` returns ``(is_valid, record_count, error)`` and is the single
source of truth for tamper evidence.  Recovery deliberately fails loudly: there
is no "reset the broken chain" path, because silently accepting a broken chain
would defeat the purpose of the audit log.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    reject_control_characters,
)

from .scrubbing import redact_secrets

GENESIS_HASH = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

#: HMAC key of an audit chain. Verification must never fall back to an
#: unkeyed SHA-256 (which a writer with file access can freely recompute).
_LOCAL_KEY_ID = "local"
_DEFAULT_KEY_ID = "default"
_MAX_KEY_CHARS = 256

#: Hard ceilings.  A privileged actor is still an untrusted input source when
#: it comes to resource usage; nothing here may grow without bound.
MAX_SEQUENCE = 1_000_000_000
MAX_TIMESTAMP_MS = 10**15
MAX_ACTION_CHARS = 128
MAX_ACTOR_CHARS = 128
MAX_STATUS_CHARS = 32
MAX_PAYLOAD_CHARS = 16 * 1024
MAX_READ_ROWS = 10_000
MAX_QUERY_RESULTS = 1_000
MAX_QUERY_OFFSET = 100_000
MAX_QUERY_WINDOW_MS = 366 * 24 * 60 * 60 * 1000
MAX_SCAN_ROWS = 100_000

GENESIS = GENESIS_HASH  # convenient back-compat alias for callers/tests


def _default_audit_path() -> str:
    """Resolve the default store location from config with a safe fallback."""
    try:
        import utils.config as config

        configured = config.get("telemetry.audit_log_path", None)
        if configured:
            return str(configured)
    except Exception:
        pass
    return str(Path.home() / ".plexichat" / "data" / "admin_audit_chain.jsonl")


def _clean_text(value: Any, field: str, maximum: int, required: bool = False) -> str:
    """Validate a short metadata field; control characters are never accepted."""
    if value is None:
        if required:
            raise HardeningViolation(f"{field} is required")
        return ""
    text = reject_control_characters(str(value), field).strip()
    if required and not text:
        raise HardeningViolation(f"{field} is required")
    if len(text) > maximum:
        raise HardeningViolation(f"{field} exceeds the length limit")
    return text


def _clean_and_redact(
    value: Any, field: str, maximum: int, required: bool = False
) -> str:
    """Validate a short metadata field and scrub secrets *before* persisting.

    ``_public_record`` redacts these fields again on read as a defence in depth
    for legacy rows, but the audit file itself must never contain the secret in
    the first place. Scrubbing here (before the entry is canonicalised and
    hashed) also keeps verification consistent: the stored entry is exactly the
    one whose checksum we commit to.
    """
    return str(redact_secrets(_clean_text(value, field, maximum, required=required)))


def _canonical(entry: Dict[str, Any]) -> str:
    """Deterministic serialization used for both writing and verifying."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class AuditChainError(RuntimeError):
    """Raised when the audit chain fails verification in strict mode."""


class AuditChain:
    """An append-only, hash-chained audit event log.

    Args:
        path: JSONL file holding the chained records.  Defaults to the
            configured ``telemetry.audit_log_path``.
        head_path: Sidecar holding ``{"count": N, "last_hash": "..."}``.
            Defaults to ``<path>.head``.
        durable: Whether to ``fsync`` after each append/head update.
    """

    def __init__(
        self,
        path: Optional[str] = None,
        head_path: Optional[str] = None,
        *,
        durable: bool = True,
        chain_key: Optional[str] = None,
        chain_key_id: Optional[str] = None,
        chain_keys: Optional[Dict[str, str]] = None,
    ) -> None:
        self.path = Path(path) if path is not None else Path(_default_audit_path())
        self.head_path = (
            Path(head_path) if head_path is not None else self.path.with_suffix(".head")
        )
        self.durable = bool(durable)
        self._thread_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local_key_path().parent.mkdir(parents=True, exist_ok=True)
        self._key_error: Optional[str] = None
        self._chain_keys, self._current_key_id = self._resolve_keys(
            chain_key, chain_key_id, chain_keys
        )

    # ------------------------------------------------------------------
    # Chain key management
    # ------------------------------------------------------------------
    @staticmethod
    def _config() -> Dict[str, Any]:
        """Return the telemetry audit config section (empty if unavailable)."""
        try:
            import utils.config as config

            cfg = config.get("telemetry.audit_log", {})
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            return {}

    def _local_key_path(self) -> Path:
        """Path of the auto-generated key sidecar (never the log itself)."""
        return Path(str(self.head_path) + ".key")

    def _chain_has_data(self) -> bool:
        """Bounded check for existing evidence before minting a fresh key."""
        try:
            head = self._read_head()
            if head["count"] > 0 or head["last_hash"] != GENESIS_HASH:
                return True
        except HardeningViolation:
            return True
        try:
            if not self.path.exists():
                return False
            with open(self.path, "rb") as handle:
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
                raise HardeningViolation("audit chain key sidecar is invalid")
            return _LOCAL_KEY_ID, text
        except FileNotFoundError:
            # Fail closed: a chain that already holds evidence must never
            # silently mint a new key, which would orphan (and effectively
            # reset verification of) every existing row. Defer the failure to
            # verification/append so ``verify_chain`` keeps its return
            # contract (and strict mode still raises) just like a corrupt
            # head does.
            if self._chain_has_data():
                self._key_error = (
                    "audit chain key sidecar is missing for a non-empty chain"
                )
                return "", ""
            key = secrets.token_hex(32)
            try:
                fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return self._load_local_key()  # another process won the race
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(key + "\n")
                handle.flush()
                if self.durable:
                    os.fsync(handle.fileno())
            return _LOCAL_KEY_ID, key

    def _resolve_keys(
        self,
        chain_key: Optional[str],
        chain_key_id: Optional[str],
        chain_keys: Optional[Dict[str, str]],
    ) -> Tuple[Dict[str, str], str]:
        """Resolve configured HMAC keys and the id to use for new rows."""
        cfg = self._config()
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
        """HMAC-SHA256 over ``prev + content`` under the named chain key."""
        key = self._chain_keys.get(key_id)
        if not key:
            raise HardeningViolation(f"audit chain key {key_id!r} is unavailable")
        data = (prev_hash + content).encode("utf-8")
        return hmac.new(key.encode("utf-8"), data, hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------
    # Serialisation: an in-process lock plus an advisory OS lock on a
    # dedicated lock file.  Holding both across "read head -> append ->
    # commit head" keeps concurrent workers from forking the chain.
    # ------------------------------------------------------------------
    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        with self._thread_lock:
            lock_path = self.path.with_suffix(".lock")
            with open(lock_path, "a+b") as lock_file:
                try:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                except (ImportError, AttributeError, OSError):
                    # Windows / restricted platforms: the in-process lock
                    # still serialises writers within a worker.
                    pass
                try:
                    yield
                finally:
                    try:
                        import fcntl

                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except (ImportError, AttributeError, OSError):
                        pass

    # ------------------------------------------------------------------
    # Head commitment
    # ------------------------------------------------------------------
    def _read_head(self) -> Dict[str, Any]:
        if not self.head_path.exists():
            return {"count": 0, "last_hash": GENESIS_HASH}
        try:
            data = json.loads(self.head_path.read_text(encoding="utf-8"))
            count = bounded_int(
                data.get("count", 0), "count", minimum=0, maximum=MAX_SEQUENCE
            )
            last_hash = str(data.get("last_hash", GENESIS_HASH))
            if not _HASH_RE.fullmatch(last_hash):
                raise HardeningViolation("audit head has an invalid hash")
            return {"count": count, "last_hash": last_hash}
        except Exception as exc:
            # Fail closed: a corrupt head is a tamper/unclean-shutdown signal,
            # never a reason to start a fresh chain and lose the old entries.
            raise HardeningViolation(f"audit head is unreadable: {exc}") from exc

    def _write_head(self, count: int, last_hash: str) -> None:
        tmp_path = self.head_path.with_suffix(".head.tmp")
        payload = json.dumps({"count": count, "last_hash": last_hash}, sort_keys=True)
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            if self.durable:
                os.fsync(handle.fileno())
        os.replace(tmp_path, self.head_path)

    # ------------------------------------------------------------------
    # Append-only writes
    # ------------------------------------------------------------------
    def append(
        self,
        action: str,
        actor_id: Any,
        *,
        target_type: Optional[str] = None,
        target_id: Any = None,
        details: Any = None,
        status: str = "success",
        timestamp_ms: Optional[int] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Append one audit event and return ``seq``/``checksum``/``prev``.

        No method on this class mutates an existing record -- ``open(..., "a")``
        is the only write mode used for the audit file itself.
        """
        clean_action = _clean_and_redact(
            action, "action", MAX_ACTION_CHARS, required=True
        )
        clean_actor = _clean_and_redact(
            actor_id, "actor_id", MAX_ACTOR_CHARS, required=True
        )
        clean_status = (
            _clean_and_redact(status, "status", MAX_STATUS_CHARS, required=True)
            or "success"
        )
        if target_type is not None:
            target_type = _clean_text(target_type, "target_type", MAX_STATUS_CHARS)
        if target_id is not None:
            target_id = _clean_text(target_id, "target_id", MAX_ACTOR_CHARS)

        ts = (
            bounded_int(
                timestamp_ms, "timestamp_ms", minimum=0, maximum=MAX_TIMESTAMP_MS
            )
            if timestamp_ms is not None
            else int(time.time() * 1000)
        )
        # Scrub *before* hashing/persisting: a secret must never reach the
        # audit file in the first place, not merely be hidden on read.
        payload: Dict[str, Any] = redact_secrets(
            {
                "target_type": target_type or None,
                "target_id": target_id or None,
                "details": details,
                "extra": extra,
            }
        )
        if len(_canonical(payload)) > MAX_PAYLOAD_CHARS:
            raise HardeningViolation("audit payload exceeds the size limit")

        with self._exclusive():
            head = self._read_head()
            seq = head["count"] + 1
            if seq > MAX_SEQUENCE:
                raise HardeningViolation("audit chain sequence exhausted")

            entry = {
                "seq": seq,
                "ts": ts,
                "action": clean_action,
                "actor": clean_actor,
                "status": clean_status,
                "payload": payload,
            }
            content = _canonical(entry)
            checksum = self._chain_checksum(
                head["last_hash"], content, self._current_key_id
            )
            # The key id names the secret that produced ``checksum``. It is
            # public metadata: an attacker who changes it to another known id
            # still fails the checksum check (HMACs under different keys differ).
            record = {
                "prev": head["last_hash"],
                "checksum": checksum,
                "key_id": self._current_key_id,
                "entry": entry,
            }
            line = json.dumps(record, sort_keys=True, separators=(",", ":"))

            # Append-only: the audit file is never opened in "w"/"r+" mode and
            # this class exposes no delete/update API.
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                if self.durable:
                    os.fsync(handle.fileno())
            self._write_head(seq, checksum)
            return {
                "seq": seq,
                "checksum": checksum,
                "prev": head["last_hash"],
                "key_id": self._current_key_id,
            }

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    @staticmethod
    def _public_record(record: Dict[str, Any]) -> Dict[str, Any]:
        """Return a safe, JSON-serializable view of a stored record."""
        entry = dict(record.get("entry") or {})
        payload = dict(entry.get("payload") or {})
        payload.pop("extra", None)
        # Defense in depth: records written by an older (unpatched) version may
        # still contain a secret, so scrub on the way out as well.
        entry["payload"] = redact_secrets(payload)
        entry["action"] = redact_secrets(entry.get("action"))
        entry["actor"] = redact_secrets(entry.get("actor"))
        return {
            "seq": entry.get("seq"),
            "checksum": record.get("checksum"),
            "prev": record.get("prev"),
            "entry": entry,
        }

    def read_all(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Read records in append order (bounded by ``limit``/``MAX_READ_ROWS``)."""
        if limit is not None:
            limit = bounded_int(limit, "limit", minimum=1, maximum=MAX_READ_ROWS)
        else:
            limit = MAX_READ_ROWS
        rows: List[Dict[str, Any]] = []
        with self._exclusive():
            if not self.path.exists():
                return rows
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    rows.append(self._public_record(json.loads(line)))
                    if len(rows) >= limit:
                        break
        return rows

    def query(
        self,
        *,
        action: Optional[str] = None,
        actor: Optional[str] = None,
        target_id: Optional[str] = None,
        since_ms: Optional[int] = None,
        until_ms: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Selective, bounded read over the chain.

        A full-file scan is never allowed: at least one selective filter (an
        exact ``action``/``actor``/``target_id`` or a bounded time window) must
        be supplied. Results and the number of inspected rows are capped, so a
        privileged caller cannot walk the whole trail in one request.
        """
        has_selector = any(
            value is not None
            for value in (action, actor, target_id, since_ms, until_ms)
        )
        if not has_selector:
            raise HardeningViolation("an indexed audit filter is required")
        if since_ms is not None and until_ms is not None:
            since_ms = bounded_int(
                since_ms, "since_ms", minimum=0, maximum=MAX_TIMESTAMP_MS
            )
            until_ms = bounded_int(
                until_ms, "until_ms", minimum=0, maximum=MAX_TIMESTAMP_MS
            )
            if until_ms < since_ms:
                raise HardeningViolation("until_ms must not precede since_ms")
            if until_ms - since_ms > MAX_QUERY_WINDOW_MS:
                raise HardeningViolation("audit query window is too wide")
        limit = bounded_int(limit, "limit", minimum=1, maximum=MAX_QUERY_RESULTS)
        offset = bounded_int(offset, "offset", minimum=0, maximum=MAX_QUERY_OFFSET)

        matches: List[Dict[str, Any]] = []
        scanned = 0
        matched = 0
        with self._exclusive():
            if not self.path.exists():
                return []
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    scanned += 1
                    if scanned > MAX_SCAN_ROWS:
                        raise HardeningViolation("audit query scan budget exceeded")
                    record = json.loads(line)
                    entry = record.get("entry") or {}
                    payload = entry.get("payload") or {}
                    if action is not None and entry.get("action") != action:
                        continue
                    if actor is not None and entry.get("actor") != actor:
                        continue
                    if target_id is not None and payload.get("target_id") != target_id:
                        continue
                    ts = entry.get("ts", 0)
                    if since_ms is not None and ts < since_ms:
                        continue
                    if until_ms is not None and ts > until_ms:
                        continue
                    matched += 1
                    if matched > offset:
                        matches.append(self._public_record(record))
                        if len(matches) >= limit:
                            break
        return matches[:limit]

    def last_checksum(self) -> str:
        """Return the current head checksum (``GENESIS_HASH`` for an empty log)."""
        with self._exclusive():
            return str(self._read_head()["last_hash"])

    # ------------------------------------------------------------------
    # Integrity verification
    # ------------------------------------------------------------------
    def verify_chain(self, *, strict: bool = False) -> Tuple[bool, int, Optional[str]]:
        """Verify the whole chain.

        Returns ``(is_valid, record_count, error)``.  With ``strict=True`` a
        broken chain raises :class:`AuditChainError` instead of returning a
        false result, for callers that must fail closed.

        Detects:
          * modified payload/metadata (keyed checksum mismatch),
          * deleted or reordered middle rows (``seq``/``prev`` mismatch),
          * tail truncation (head count/hash mismatch),
          * a missing log file while the head still claims rows,
          * a missing/unknown chain key or an unkeyed (downgraded) row.
        """
        try:
            with self._exclusive():
                head = self._read_head()
                if self._key_error:
                    return self._fail(strict, 0, self._key_error)
                if not self.path.exists():
                    if head["count"] != 0:
                        return self._fail(
                            strict,
                            0,
                            "audit log file is missing while head claims rows",
                        )
                    return True, 0, None

                expected_prev = GENESIS_HASH
                count = 0
                with open(self.path, "r", encoding="utf-8") as handle:
                    for line_num, line in enumerate(handle, 1):
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                        except (ValueError, TypeError) as exc:
                            return self._fail(
                                strict, count, f"invalid JSON at line {line_num}: {exc}"
                            )
                        if not isinstance(record, dict):
                            return self._fail(
                                strict, count, f"invalid record at line {line_num}"
                            )
                        entry = record.get("entry") or {}
                        if not isinstance(entry, dict):
                            return self._fail(
                                strict, count, f"invalid entry at line {line_num}"
                            )
                        if entry.get("seq") != count + 1:
                            return self._fail(
                                strict, count, f"sequence gap at line {line_num}"
                            )
                        if record.get("prev") != expected_prev:
                            return self._fail(
                                strict, count, f"prev_hash mismatch at line {line_num}"
                            )
                        # Every row must name a key we actually hold. A row with
                        # no key_id would be verifiable with public data alone
                        # (plain SHA-256), so accepting one in this chain would
                        # let a writer downgrade keying and rewrite history.
                        key_id = record.get("key_id")
                        if not isinstance(key_id, str) or not key_id:
                            return self._fail(
                                strict,
                                count,
                                f"missing chain key id at line {line_num}",
                            )
                        if key_id not in self._chain_keys:
                            return self._fail(
                                strict,
                                count,
                                f"unknown chain key id {key_id!r} at line {line_num}",
                            )
                        calculated = self._chain_checksum(
                            expected_prev, _canonical(entry), key_id
                        )
                        stored = record.get("checksum")
                        # Constant-time: a short-circuiting ``!=`` leaks a byte-
                        # by-byte oracle for a forged checksum to a verifier
                        # with repeated access. The isinstance guard keeps a
                        # missing/non-string checksum from raising out of here.
                        if not (
                            isinstance(stored, str)
                            and hmac.compare_digest(calculated, stored)
                        ):
                            return self._fail(
                                strict, count, f"checksum mismatch at line {line_num}"
                            )
                        expected_prev = stored
                        count += 1

                if count != head["count"]:
                    return self._fail(
                        strict,
                        count,
                        f"entry count {count} does not match head count {head['count']}",
                    )
                if expected_prev != head["last_hash"]:
                    return self._fail(
                        strict, count, "head hash mismatch (tail truncation)"
                    )
                return True, count, None
        except HardeningViolation as exc:
            return self._fail(strict, 0, str(exc))

    @staticmethod
    def _fail(strict: bool, count: int, error: str) -> Tuple[bool, int, Optional[str]]:
        if strict:
            raise AuditChainError(error)
        return False, count, error

    def assert_intact(self) -> int:
        """Verify the chain and return the row count, failing closed on tamper.

        Callers that consume audit data (exports, compliance reports) should
        use this rather than ignoring a ``verify_chain()`` false result.
        """
        self.verify_chain(strict=True)
        return int(self._read_head()["count"])


def verify_audit_chain(
    path: Optional[str] = None, *, strict: bool = False
) -> Tuple[bool, int, Optional[str]]:
    """Convenience wrapper: verify the chain at ``path`` (or the default path)."""
    return AuditChain(path).verify_chain(strict=strict)


#: Security-relevant admin actions. Every one of these must leave an audit
#: entry. Keeping the registry explicit (rather than trusting each call site
#: to remember to log) lets tests enumerate the complete privileged surface and
#: fail when a new action is added without audit coverage.
PRIVILEGED_ACTIONS = frozenset(
    {
        "login",
        "logout",
        "password_change",
        "otp_setup",
        "role_assign",
        "role_revoke",
        "user.ban",
        "user.unban",
        "user.role_change",
        "user.delete",
        "server.delete",
        "channel.delete",
        "config.update",
        "config.secret_rotate",
        "approval.approve",
        "approval.reject",
        "security.revoke_sessions",
        "token.revoke",
        "license.update",
    }
)


def record_privileged_action(
    store: AuditChain, action: str, actor_id: Any, **kwargs: Any
) -> Dict[str, Any]:
    """Append an audit entry for a registered privileged action.

    Fails closed for an unknown action so a newly added privileged path cannot
    silently bypass the audit trail.
    """
    if action not in PRIVILEGED_ACTIONS:
        raise HardeningViolation(
            f"action is not a registered privileged action: {action}"
        )
    return store.append(action=action, actor_id=actor_id, **kwargs)


def missing_privileged_actions(store: AuditChain) -> frozenset:
    """Return the privileged actions with no entry in ``store``."""
    seen = {row["entry"].get("action") for row in store.read_all(limit=MAX_READ_ROWS)}
    return frozenset(PRIVILEGED_ACTIONS - seen)


__all__ = [
    "GENESIS_HASH",
    "MAX_SEQUENCE",
    "AuditChain",
    "AuditChainError",
    "verify_audit_chain",
    "PRIVILEGED_ACTIONS",
    "record_privileged_action",
    "missing_privileged_actions",
]
