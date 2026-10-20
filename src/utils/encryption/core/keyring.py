"""
Keyring manager - Manages encryption keys at rest.

Supports multiple key versions for rotation, encrypted storage using AES-GCM,
and KEK (Key Encryption Key) via environment variables or HSM/TPM vault.
"""

import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import utils.logger as logger

from .file_lock import acquire_file_lock, release_file_lock


class KeyringDecryptionError(RuntimeError):
    """Raised when a keyring file cannot be decrypted (KEK mismatch or corruption)."""

    pass


# Pepper ids double as password-hash envelope labels; keep the charset tight
# (must match the envelope regex in `src.utils.encryption.pepper`).
_PEPPER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")

# ``acquire_file_lock`` is non-blocking by contract, so a caller that needs
# mutual exclusion has to retry. Writes are short, so a 30s contention timeout
# only fires when a peer process is wedged mid-write (a hung writer, not a busy
# one), and failing loudly beats writing without the lock.
_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_RETRY_SECONDS = 0.02


class Keyring:
    """
    Manages multiple versions of encryption keys for rotation support.
    Keys are encrypted at rest using a Key Encryption Key (KEK).

    Thread-safe within a process and uses file locking for multi-process safety.

    Each keyring can have its own dedicated KEK for enhanced security:
    - system_keyring.json: PLEXICHAT_SYSTEM_KEY (or fallback)
    - message_keyring.json: PLEXICHAT_MESSAGE_KEY (or fallback)
    - file_keyring.json: PLEXICHAT_MEDIA_KEY (or fallback)
    """

    def __init__(
        self,
        keyring_path: Path,
        kek_env_var: Optional[str] = None,
        fallback_to_system: bool = True,
    ):
        self.path = keyring_path
        self.kek_env_var = kek_env_var or self._default_kek_env_var(keyring_path)
        self.fallback_to_system = fallback_to_system
        self.lock_path = keyring_path.with_suffix(".lock")
        self.current_version: int = 0
        self.current_key_source: str = "unknown"
        self.keys: Dict[int, bytes] = {}
        self.blind_index_root_key: Optional[bytes] = None
        # Versioned password-pepper secrets (pepper_id -> 32 bytes). Stored
        # alongside the data keys so they are encrypted at rest by this
        # keyring's KEK; generated once via CSRNG and never derived.
        self.pepper_keys: Dict[str, bytes] = {}
        self.rotated_at: int = 0
        self._thread_lock = threading.Lock()
        self.load()

        # Initialize blind index root key if it doesn't exist
        if not self.blind_index_root_key and self.keys:
            self.blind_index_root_key = self._derive_blind_index_root_key()
            self.save()

    def _derive_blind_index_root_key(self) -> bytes:
        """Deterministically derive the blind-index root key from the KEK.

        Derived (rather than randomly generated) so that every
        :class:`EncryptionManager` instance in the process computes the
        same blind index regardless of *when* its keyring was loaded. The
        previous ``secrets.token_bytes(32)`` generation produced a new key
        each time a keyring was loaded after keys already existed, so a
        long-lived module singleton (created before any keys) disagreed
        with later-created managers and lookups silently failed.
        """
        kek = self._get_kek(fallback=False)
        return hashlib.blake2b(
            kek, key=b"plexichat:blind-index-root:v1", digest_size=32
        ).digest()

    def get_blind_index_root_key(self) -> bytes:
        """Return the blind-index root key, deriving and persisting it lazily.

        Derived deterministically from the KEK so every manager instance
        agrees, and persisted so it also survives KEK rotation (a manager
        created later loads the persisted value instead of re-deriving).
        """
        if self.blind_index_root_key is None:
            self.blind_index_root_key = self._derive_blind_index_root_key()
            self.save()
        return self.blind_index_root_key

    @staticmethod
    def _default_kek_env_var(keyring_path: Path) -> str:
        """Select the production KEK for known keyring filenames."""
        keyring_name = keyring_path.name
        if keyring_name == "message_keyring.json":
            return "PLEXICHAT_MESSAGE_KEY"
        if keyring_name == "file_keyring.json":
            return "PLEXICHAT_MEDIA_KEY"
        return "PLEXICHAT_SYSTEM_KEY"

    def _detect_current_key_source(self, stored_source: Optional[str] = None) -> str:
        """Infer where the current key came from.

        SECURITY: the previous implementation returned the literal
        string ``"generated"`` from every branch, which made
        startup-time KEK-source warnings (e.g. "we are running on a
        machine-local fallback key") completely broken. We now introspect
        the live vault and KEK-resolution order to report a truthful
        source label.
        """
        if stored_source and stored_source in {"env", "hsm", "tpm", "local"}:
            return stored_source

        # Walk the real KEK resolution order so we know which backing
        # store the live key actually came from.
        try:
            from ..vault import HardwareVault

            # Try the dedicated KEK first; fall back to the system KEK so
            # operators running the off-spec shared key path still see the
            # correct label.
            for env in (self.kek_env_var, "PLEXICHAT_SYSTEM_KEY"):
                try:
                    probe = HardwareVault(kek_env_var=env)
                    src = probe.get_source()
                    if src in {"env", "hsm", "tpm", "local"}:
                        return src
                except Exception:
                    continue
        except Exception:
            pass

        # If we cannot probe the vault, only fall back to the persisted
        # metadata value if it is one of the legitimate labels.  Any
        # other stored label (e.g. ``generated``) is treated as
        # ``unknown`` rather than mis-reported as secure.
        if stored_source == "generated":
            return "generated"

        return "unknown"

    def _get_kek(self, fallback: bool = False) -> bytes:
        """
        Get the Key Encryption Key (KEK) for this keyring.

        Args:
            fallback: If True, try PLEXICHAT_SYSTEM_KEY as fallback

        Returns:
            The KEK bytes
        """
        from ..vault import HardwareVault

        # Try dedicated KEK first
        try:
            vault_instance = HardwareVault(kek_env_var=self.kek_env_var)
            kek = vault_instance.get_kek()
            if kek:
                return kek
        except Exception as e:
            if not fallback:
                raise KeyringDecryptionError(
                    f"Failed to get KEK from {self.kek_env_var}: {e}"
                )
            logger.warning(
                f"Failed to get KEK from {self.kek_env_var}, trying fallback: {e}"
            )

        # Fallback to system KEK if enabled
        if fallback and self.kek_env_var != "PLEXICHAT_SYSTEM_KEY":
            try:
                vault_instance = HardwareVault(kek_env_var="PLEXICHAT_SYSTEM_KEY")
                kek = vault_instance.get_kek()
                if kek:
                    logger.info(
                        f"Using fallback KEK from PLEXICHAT_SYSTEM_KEY for {self.path.name}"
                    )
                    return kek
            except Exception as e:
                logger.error(f"Fallback KEK retrieval failed: {e}")

        raise KeyringDecryptionError(
            f"Unable to retrieve KEK for keyring {self.path.name}. "
            f"Ensure {self.kek_env_var} is set or configure HSM/TPM."
        )

    def _with_file_lock(self, func, *args, **kwargs):
        """Execute function with both thread and file lock.

        The lock file is opened in BINARY append mode ("ab") rather than
        text-write mode ("w"):
        - Binary mode is required by ``msvcrt.locking`` on Windows, which
          operates on raw bytes - text-mode translation can shift the
          byte offsets silently and cause the lock to miss the byte range.
        - Append mode avoids truncation. Truncating the existing lock
          file between acquire/release races with other processes that
          may have already opened the file via its previous inode.
        """
        with self._thread_lock:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.lock_path, "ab") as lock_file:
                # ``acquire_file_lock`` reports contention rather than waiting
                # for it, and the return value used to be ignored: a second
                # process (an xdist worker, or two server processes) proceeded
                # without the lock and both wrote the same temporary file, so
                # one of them lost it between ``open`` and ``os.replace`` and
                # raised FileNotFoundError. Wait for the lock instead.
                deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
                while not acquire_file_lock(lock_file, exclusive=True):
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"timed out after {_LOCK_TIMEOUT_SECONDS:g}s waiting for "
                            f"the keyring lock {self.lock_path}"
                        )
                    time.sleep(_LOCK_RETRY_SECONDS)
                try:
                    return func(*args, **kwargs)
                finally:
                    release_file_lock(lock_file)

    def load(self):
        """Load encrypted keyring from disk with KEK fallback."""
        self._with_file_lock(self._load_from_disk)

    def _load_from_disk(self):
        """Decrypt and parse the keyring file (caller must hold the lock)."""
        if not self.path.exists():
            return

        try:
            with open(self.path, "r") as f:
                encrypted_data = json.load(f)

            # Decrypt the keyring payload
            payload = base64.b64decode(encrypted_data["payload"])
            nonce = base64.b64decode(encrypted_data["nonce"])

            # Try dedicated KEK first, then the system KEK for old keyrings
            try:
                kek = self._get_kek(fallback=False)
                decrypted = AESGCM(kek).decrypt(nonce, payload, None)
            except Exception:
                if not (
                    self.fallback_to_system
                    and self.kek_env_var != "PLEXICHAT_SYSTEM_KEY"
                ):
                    raise
                logger.warning(
                    f"Failed to decrypt {self.path.name} with {self.kek_env_var}, trying PLEXICHAT_SYSTEM_KEY fallback"
                )
                system_kek = self._get_kek(fallback=True)
                decrypted = AESGCM(system_kek).decrypt(nonce, payload, None)

            data = json.loads(decrypted)

            self.current_version = data.get("current_version", 0)
            self.rotated_at = data.get("rotated_at", 0)
            self.keys = {
                int(v): base64.b64decode(k) for v, k in data.get("keys", {}).items()
            }
            self.blind_index_root_key = (
                base64.b64decode(data["blind_index_root_key"])
                if data.get("blind_index_root_key")
                else None
            )
            raw_peppers = data.get("pepper_keys", {}) or {}
            self.pepper_keys = {
                str(pid): base64.b64decode(secret)
                for pid, secret in raw_peppers.items()
            }
            self.current_key_source = self._detect_current_key_source(
                data.get("current_key_source")
            )
            logger.info(
                f"Loaded keyring {self.path.name} with {len(self.keys)} key version(s)"
            )
        except Exception as e:
            logger.critical(
                f"FATAL: Failed to decrypt keyring at {self.path}: {e}. "
                f"This usually means the KEK has changed since the keyring was created. "
                f"Restore the original keyring files and KEK from backup, "
                f"or set the correct KEK environment variable. "
                f"Keyring files: ~/.plexichat/data/system_keyring.json, "
                f"~/.plexichat/data/file_keyring.json, "
                f"~/.plexichat/data/message_keyring.json."
            )

            # If keyring decryption fails, the server cannot operate safely.
            try:
                from src.core.database import invalidate_pattern

                count = invalidate_pattern("*")
                logger.warning(
                    f"KEK change detected or keyring corrupted. Invalidated {count} cache keys."
                )
            except Exception as ce:
                logger.error(
                    f"Failed to invalidate cache after keyring decryption failure: {ce}"
                )

            self.current_version = 0
            self.current_key_source = "unknown"
            self.keys = {}
            self.rotated_at = 0
            self.pepper_keys = {}

            raise KeyringDecryptionError(
                f"Keyring decryption failed for {self.path}: {e}. "
                f"Restore keyring files from backup or set correct KEK."
            )

    def save(self):
        """Save keyring to disk, encrypted with KEK (acquires file lock)."""
        self._with_file_lock(
            lambda: self._write_atomic(include_blind_index_root_key=True)
        )

    def _write_atomic(self, include_blind_index_root_key: bool = False) -> None:
        """Build, encrypt, and atomically write the keyring payload.

        Caller is responsible for holding the appropriate lock (file+thread
        for ``save()``, just thread for ``_save_without_lock()``).

        Args:
            include_blind_index_root_key: When True, persist the
                ``blind_index_root_key`` field. This is required for
                full keyring saves (e.g. from ``__init__``) but skipped
                by the locked helper used by ``get_key()``/``rotate()``
                which only mutate the key/version metadata.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)

        raw_data = {
            "current_version": self.current_version,
            "current_key_source": self.current_key_source,
            "rotated_at": self.rotated_at,
            "keys": {
                str(v): base64.b64encode(k).decode("utf-8")
                for v, k in self.keys.items()
            },
            # Pepper secrets are key material: always persisted (never gated
            # by the blind-index flag) so they survive blind-index-only
            # writes and KEK-rotation re-encryption alike.
            "pepper_keys": {
                str(pid): base64.b64encode(secret).decode("utf-8")
                for pid, secret in self.pepper_keys.items()
            },
        }

        if include_blind_index_root_key:
            raw_data["blind_index_root_key"] = (
                base64.b64encode(self.blind_index_root_key).decode("utf-8")
                if self.blind_index_root_key
                else None
            )

        # Encrypt the keyring with current KEK
        kek = self._get_kek(fallback=False)
        aesgcm = AESGCM(kek)
        nonce = os.urandom(12)
        payload = aesgcm.encrypt(nonce, json.dumps(raw_data).encode(), None)

        final_data = {
            "nonce": base64.b64encode(nonce).decode(),
            "payload": base64.b64encode(payload).decode(),
        }

        # A unique temporary file per write: a fixed `<name>.tmp` was shared by
        # every process writing this keyring, so two concurrent saves could
        # replace the same path and the slower one would find it already moved.
        fd, temp_name = tempfile.mkstemp(
            prefix=f"{self.path.stem}.", suffix=".tmp", dir=str(self.path.parent)
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(final_data, f)

            # Restrict permissions (Unix only, Windows ignores)
            try:
                os.chmod(temp_path, 0o600)
            except (OSError, AttributeError):
                pass

            # Atomic swap
            os.replace(temp_path, self.path)
        finally:
            # Reached only if the swap did not happen (write/chmod failure).
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    def get_or_create_pepper(self, pepper_id: str) -> bytes:
        """Return the 32-byte pepper for ``pepper_id``, generating it on first use.

        The secret is generated with a CSRNG, persisted in this keyring
        (encrypted at rest by the keyring's KEK), and stable afterwards.
        The file is re-read under the lock first so concurrent processes or
        workers racing the first use converge on a single value instead of
        each persisting their own.
        """

        def _impl():
            if not _PEPPER_ID_RE.fullmatch(pepper_id):
                raise ValueError(f"invalid pepper id: {pepper_id!r}")
            # Refresh under the lock: another worker may have created this
            # pepper (or any other state) since this instance loaded.
            self._load_from_disk()
            existing = self.pepper_keys.get(pepper_id)
            if existing is not None:
                if len(existing) != 32:
                    raise KeyringDecryptionError(
                        f"pepper '{pepper_id}' in {self.path.name} is corrupt "
                        f"({len(existing)} bytes, expected 32)"
                    )
                return existing
            new_secret = secrets.token_bytes(32)
            self.pepper_keys[pepper_id] = new_secret
            logger.info(
                f"Generated new password pepper '{pepper_id}' in {self.path.name}"
            )
            self._save_without_lock()
            return new_secret

        return self._with_file_lock(_impl)

    def list_peppers(self) -> List[str]:
        """Return the sorted ids of stored password peppers."""

        def _impl():
            self._load_from_disk()
            return sorted(self.pepper_keys.keys())

        return self._with_file_lock(_impl)

    def get_key(self, version: Optional[int] = None) -> Tuple[int, bytes]:
        """Get a specific key version or the current one."""

        def _get_key_impl():
            if not self.keys:
                # Keyring is empty, generate new key
                new_key = AESGCM.generate_key(bit_length=256)
                self.current_version = 1
                self.current_key_source = "generated"
                self.keys[1] = new_key
                self.rotated_at = int(time.time())
                logger.info(f"Generated new key for keyring {self.path.name}")
                self._save_without_lock()

            v = (
                version
                if version is not None and version != 0
                else self.current_version
            )
            key = self.keys.get(v)
            if not key:
                raise ValueError(
                    f"Key version {v} not found in keyring "
                    f"(loaded versions: {sorted(self.keys)})"
                )
            return v, key

        return self._with_file_lock(_get_key_impl)

    def _save_without_lock(self):
        """Internal save without acquiring lock (caller must hold lock)."""
        self._write_atomic(include_blind_index_root_key=False)

    def rotate(self) -> int:
        """Generate a new key version and make it current."""

        def _rotate_impl():
            new_version = self.current_version + 1
            new_key = AESGCM.generate_key(bit_length=256)
            self.keys[new_version] = new_key
            self.current_version = new_version
            self.current_key_source = "generated"
            self.rotated_at = int(time.time())
            logger.info(f"Rotated encryption key to version {new_version}")
            self._save_without_lock()
            return self.current_version

        return self._with_file_lock(_rotate_impl)

    def wrap(self, raw: bytes) -> str:
        """Encrypt ``raw`` with the current key and return a v2 envelope.

        Used by callers that need to wrap small key blobs (for example,
        channel-ratchet ``start_key`` material) at rest. The output is
        in the standard ``ENC:{version}:{base64(nonce||ct||tag)}``
        format and can be unwrapped with :meth:`unwrap`.
        """
        version, key = self.get_key()
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, raw, None)
        return f"ENC:{version}:{base64.b64encode(nonce + ciphertext).decode('utf-8')}"

    def unwrap(self, wrapped: str) -> bytes:
        """Reverse of :meth:`wrap`."""
        if not wrapped.startswith("ENC:"):
            raise KeyringDecryptionError("wrapped value is missing the ENC: prefix")
        version: Optional[int] = None
        try:
            parts = wrapped.split(":", 2)
            version = int(parts[1])
            _, key = self.get_key(version)
            blob = base64.b64decode(parts[2])
            nonce, ciphertext = blob[:12], blob[12:]
            return AESGCM(key).decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise KeyringDecryptionError(
                f"failed to unwrap value (key version {version}): {exc}"
            ) from exc
