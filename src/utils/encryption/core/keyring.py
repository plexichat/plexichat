"""
Keyring manager - Manages encryption keys at rest.

Supports multiple key versions for rotation, encrypted storage using AES-GCM,
and KEK (Key Encryption Key) via environment variables or HSM/TPM vault.
"""

import base64
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import utils.logger as logger

from .file_lock import acquire_file_lock, release_file_lock


class KeyringDecryptionError(RuntimeError):
    """Raised when a keyring file cannot be decrypted (KEK mismatch or corruption)."""

    pass


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
        self.rotated_at: int = 0
        self._thread_lock = threading.Lock()
        self.load()

        # Initialize blind index root key if it doesn't exist
        if not self.blind_index_root_key and self.keys:
            self.blind_index_root_key = secrets.token_bytes(32)
            self.save()

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
                acquire_file_lock(lock_file, exclusive=True)
                try:
                    return func(*args, **kwargs)
                finally:
                    release_file_lock(lock_file)

    def load(self):
        """Load encrypted keyring from disk with KEK fallback."""

        def _load_impl():
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

                raise KeyringDecryptionError(
                    f"Keyring decryption failed for {self.path}: {e}. "
                    f"Restore keyring files from backup or set correct KEK."
                )

        self._with_file_lock(_load_impl)

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

        temp_path = self.path.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            json.dump(final_data, f)

        # Restrict permissions (Unix only, Windows ignores)
        try:
            os.chmod(temp_path, 0o600)
        except (OSError, AttributeError):
            pass

        # Atomic swap
        os.replace(temp_path, self.path)

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
                raise ValueError(f"Key version {v} not found in keyring")
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
        try:
            parts = wrapped.split(":", 2)
            version = int(parts[1])
            _, key = self.get_key(version)
            blob = base64.b64decode(parts[2])
            nonce, ciphertext = blob[:12], blob[12:]
            return AESGCM(key).decrypt(nonce, ciphertext, None)
        except Exception as exc:
            raise KeyringDecryptionError(f"failed to unwrap value: {exc}") from exc
