import base64
import hashlib
import os
import time
import json
import threading
import sys
from pathlib import Path
from typing import Optional, Tuple, Dict
import importlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    import xxhash

    XXHASH_AVAILABLE = True
except ImportError:
    xxhash = None
    XXHASH_AVAILABLE = False

try:
    _argon2 = importlib.import_module("argon2")
    _argon2_exceptions = importlib.import_module("argon2.exceptions")
    PasswordHasher = _argon2.PasswordHasher
    VerifyMismatchError = _argon2_exceptions.VerifyMismatchError
    VerificationError = _argon2_exceptions.VerificationError
    InvalidHash = _argon2_exceptions.InvalidHash
except Exception:
    PasswordHasher = None
    VerifyMismatchError = Exception
    VerificationError = Exception
    InvalidHash = Exception

import utils.logger as logger
from .vault import vault


def _acquire_file_lock(lock_file, exclusive: bool = True) -> bool:
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


def _release_file_lock(lock_file) -> None:
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


class Keyring:
    """
    Manages multiple versions of encryption keys for rotation support.
    Keys are encrypted at rest using a Master Key (KEK).

    Thread-safe within a process and uses file locking for multi-process safety.
    """

    def __init__(self, keyring_path: Path, env_var: Optional[str] = None):
        self.path = keyring_path
        self.env_var = env_var
        self.lock_path = keyring_path.with_suffix(".lock")
        self.current_version: int = 0
        self.keys: Dict[int, bytes] = {}
        self.rotated_at: int = 0
        self._thread_lock = threading.Lock()
        self.load()

    def _get_kek(self) -> bytes:
        return vault.get_kek()

    def _get_env_key(self) -> Optional[bytes]:
        if not self.env_var:
            return None

        val = os.environ.get(self.env_var)
        if not val:
            return None

        try:
            # Expecting Base64 encoded 32-byte key
            key = base64.b64decode(val)
            if len(key) != 32:
                logger.warning(
                    f"Environment variable {self.env_var} must be a 32-byte key (Base64 encoded)"
                )
                return None
            return key
        except Exception as e:
            logger.warning(f"Failed to decode environment variable {self.env_var}: {e}")
            return None

    def _with_file_lock(self, func, *args, **kwargs):
        """Execute function with both thread and file lock."""
        with self._thread_lock:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.lock_path, "w") as lock_file:
                _acquire_file_lock(lock_file, exclusive=True)
                try:
                    return func(*args, **kwargs)
                finally:
                    _release_file_lock(lock_file)

    def load(self):
        """Load encrypted keyring from disk."""

        def _load_impl():
            if not self.path.exists():
                return

            try:
                with open(self.path, "r") as f:
                    encrypted_data = json.load(f)

                kek = self._get_kek()
                aesgcm = AESGCM(kek)

                # Decrypt the keyring payload
                payload = base64.b64decode(encrypted_data["payload"])
                nonce = base64.b64decode(encrypted_data["nonce"])

                decrypted = aesgcm.decrypt(nonce, payload, None)
                data = json.loads(decrypted)

                self.current_version = data.get("current_version", 0)
                self.rotated_at = data.get("rotated_at", 0)
                self.keys = {
                    int(v): base64.b64decode(k) for v, k in data.get("keys", {}).items()
                }
            except Exception as e:
                logger.error(f"CRITICAL: Failed to decrypt keyring at {self.path}: {e}")
                self.current_version = 0
                self.keys = {}
                self.rotated_at = 0
                return

        self._with_file_lock(_load_impl)

    def save(self):
        """Save keyring to disk, encrypted with KEK."""

        def _save_impl():
            self.path.parent.mkdir(parents=True, exist_ok=True)

            raw_data = {
                "current_version": self.current_version,
                "rotated_at": self.rotated_at,
                "keys": {
                    str(v): base64.b64encode(k).decode("utf-8")
                    for v, k in self.keys.items()
                },
            }

            # Encrypt the keyring
            kek = self._get_kek()
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

        self._with_file_lock(_save_impl)

    def get_key(self, version: Optional[int] = None) -> Tuple[int, bytes]:
        """Get a specific key version or the current one."""

        def _get_key_impl():
            if not self.keys:
                # 1. Try environment variable override
                env_key = self._get_env_key()
                if env_key:
                    self.current_version = 1
                    self.keys[1] = env_key
                    self.rotated_at = int(time.time())
                    logger.info(
                        f"Initialized keyring with key from environment variable {self.env_var}"
                    )
                    self._save_without_lock()
                else:
                    # 2. Generate new random key
                    new_key = AESGCM.generate_key(bit_length=256)
                    self.current_version = 1
                    self.keys[1] = new_key
                    self.rotated_at = int(time.time())
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
        self.path.parent.mkdir(parents=True, exist_ok=True)

        raw_data = {
            "current_version": self.current_version,
            "rotated_at": self.rotated_at,
            "keys": {
                str(v): base64.b64encode(k).decode("utf-8")
                for v, k in self.keys.items()
            },
        }

        kek = self._get_kek()
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

        os.replace(temp_path, self.path)

    def rotate(self) -> int:
        """Generate a new key version and make it current."""

        def _rotate_impl():
            new_version = self.current_version + 1
            new_key = AESGCM.generate_key(bit_length=256)
            self.keys[new_version] = new_key
            self.current_version = new_version
            self.rotated_at = int(time.time())
            logger.info(f"Rotated encryption key to version {new_version}")
            self._save_without_lock()
            return self.current_version

        return self._with_file_lock(_rotate_impl)


class EncryptionManager:
    """
    Hardened Encryption Manager.
    Uses Argon2id for passwords and AES-256-GCM for data.
    """

    def __init__(
        self,
        argon2_time_cost=2,
        argon2_memory_cost=65536,
        argon2_parallelism=2,
        argon2_hash_length: int = 32,
        argon2_salt_length: int = 16,
    ):
        if PasswordHasher is None:
            raise RuntimeError("argon2 is required for password hashing")
        self._argon2_hash_length = int(argon2_hash_length)
        self._argon2_salt_length = int(argon2_salt_length)
        self.password_hasher = PasswordHasher(
            time_cost=argon2_time_cost,
            memory_cost=argon2_memory_cost,
            parallelism=argon2_parallelism,
            hash_len=self._argon2_hash_length,
            salt_len=self._argon2_salt_length,
        )
        self.keyring = Keyring(
            Path.home() / ".plexichat" / "data" / "system_keyring.json",
            env_var="PLEXICHAT_ENCRYPTION_KEY",
        )

    def derive_key(
        self,
        password: str,
        salt: Optional[bytes] = None,
        iterations: int = 100_000,
        length: int = 32,
    ):
        if not password:
            raise ValueError("Empty password")
        if salt is None:
            salt = os.urandom(16)
        if len(salt) < 16:
            raise ValueError("Salt too short")
        if length <= 0:
            raise ValueError("Invalid key length")

        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations), dklen=int(length)
        )
        return key, salt

    def hash_password(self, password: str) -> str:
        if not password:
            raise ValueError("Empty password")
        return self.password_hasher.hash(password)

    def verify_password(self, password: str, hash_str: str) -> bool:
        try:
            self.password_hasher.verify(hash_str, password)
            return True
        except (VerifyMismatchError, VerificationError, InvalidHash):
            return False

    def encrypt_data(
        self, data: str, key: Optional[bytes] = None, context: Optional[str] = None
    ) -> str:
        """
        Encrypt data using current keyring version or provided key, optionally binding to context (AAD).
        """
        if key is not None:
            if len(key) != 32:
                raise ValueError("Key must be 32 bytes")
            aesgcm = AESGCM(key)
            version = 0
        else:
            version, key = self.keyring.get_key()
            aesgcm = AESGCM(key)

        nonce = os.urandom(12)
        aad = context.encode("utf-8") if context else None
        ciphertext = aesgcm.encrypt(nonce, data.encode("utf-8"), aad)
        combined = nonce + ciphertext
        encoded = base64.b64encode(combined).decode("utf-8")
        return f"ENC:{version}:{encoded}"

    def decrypt_data(
        self,
        encrypted_data: str,
        key: Optional[bytes] = None,
        context: Optional[str] = None,
    ) -> str:
        if encrypted_data == "":
            return ""

        if not encrypted_data.startswith("ENC:"):
            raise ValueError("Malformed encrypted data")

        parts = encrypted_data.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise ValueError("Malformed encrypted data")
        version = int(parts[1])
        data_to_decode = parts[2]

        if key is not None and len(key) != 32:
            raise ValueError("Key must be 32 bytes")

        if not key:
            _, key = self.keyring.get_key(version)

        aesgcm = AESGCM(key)
        try:
            combined = base64.b64decode(data_to_decode)
        except Exception as e:
            raise ValueError(f"Malformed encrypted data: {e}")
        if len(combined) < 28:
            raise ValueError("Malformed encrypted data")
        nonce = combined[:12]
        ciphertext = combined[12:]
        aad = context.encode("utf-8") if context else None

        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
            return plaintext.decode("utf-8")
        except Exception as e:
            logger.error(f"Decryption failed: {repr(e)}")
            raise ValueError(
                "Integrity check failed: Data may have been tampered with."
            )

    def blind_index(self, data: str, scope: str) -> str:
        """
        Generate a keyed hash for searching encrypted fields.
        """
        kek = self.keyring._get_kek()
        # Use KEK + Scope to derive an index key
        index_key = hashlib.blake2b(kek, key=scope.encode(), digest_size=32).digest()
        return hashlib.blake2b(
            data.lower().strip().encode(), key=index_key, digest_size=32
        ).hexdigest()

    def fast_blind_index(self, data: str, scope: str) -> str:
        """
        Generate a secure keyed hash for high-volume enforcement fields.
        
        SECURITY: This previously used xxhash for performance, but xxhash is 
        non-cryptographic and allows easy brute-force deanonymization of 
        small spaces like IP addresses. We now use a truncated BLAKE2b
        which is still very fast but cryptographically secure.
        """
        return self.blind_index(data, scope)

    def rotate_keys(self, force: bool = False) -> bool:
        """Rotate keys if enough time has passed."""
        import utils.config as config

        # Get rotation interval from config (default 90 days)
        rotation_days = config.get("encryption.key_rotation_days", 90)
        rotation_seconds = rotation_days * 24 * 60 * 60

        current_time = int(time.time())
        time_since_rotation = current_time - self.keyring.rotated_at

        if force or time_since_rotation >= rotation_seconds:
            self.keyring.rotate()
            logger.info(
                f"Encryption keys rotated after {time_since_rotation // 86400} days"
            )
            return True

        logger.debug(
            f"Key rotation not needed - {time_since_rotation // 86400} days since last rotation"
        )
        return False


class SnowflakeGenerator:
    """
    Twitter-style Snowflake ID generator.
    Format: [1-bit unused][41-bit timestamp][5-bit datacenter][5-bit worker][12-bit sequence]

    For single-machine deployments, auto-generates IDs from machine characteristics.
    For distributed deployments, set PLEXICHAT_WORKER_ID and PLEXICHAT_DATACENTER_ID.
    """

    def __init__(
        self,
        worker_id: Optional[int] = None,
        datacenter_id: Optional[int] = None,
        epoch_timestamp: Optional[int] = None,
    ):
        # Epoch: 2024-01-01 00:00:00 UTC
        self.epoch = epoch_timestamp or 1704067200000

        # Auto-derive IDs if not provided
        if worker_id is None:
            worker_id = self._get_auto_worker_id()
        if datacenter_id is None:
            datacenter_id = self._get_auto_datacenter_id()

        # Validate bounds
        if not (0 <= worker_id <= 31):
            raise ValueError(f"worker_id must be 0-31, got {worker_id}")
        if not (0 <= datacenter_id <= 31):
            raise ValueError(f"datacenter_id must be 0-31, got {datacenter_id}")

        self.worker_id = worker_id & 0x1F
        self.datacenter_id = datacenter_id & 0x1F
        self.sequence = 0
        self.last_timestamp = -1
        self._lock = threading.Lock()

        logger.debug(
            f"SnowflakeGenerator initialized: worker={self.worker_id}, datacenter={self.datacenter_id}"
        )

    def _get_auto_worker_id(self) -> int:
        """Auto-derive worker ID from environment or machine characteristics."""
        # 1. Check environment variable
        env_id = os.environ.get("PLEXICHAT_WORKER_ID")
        if env_id is not None:
            try:
                return int(env_id) % 32
            except ValueError:
                pass

        # 2. Try to derive from process ID and hostname for uniqueness
        import socket

        try:
            hostname = socket.gethostname()
            # Combine hostname hash with PID for multi-process on same machine
            host_hash = hashlib.sha256(hostname.encode()).digest()
            pid_component = os.getpid() % 8  # 3 bits from PID
            host_component = host_hash[0] % 4  # 2 bits from hostname
            return (host_component << 3) | pid_component
        except Exception:
            return 1

    def _get_auto_datacenter_id(self) -> int:
        """Auto-derive datacenter ID from environment or machine characteristics."""
        # 1. Check environment variable
        env_id = os.environ.get("PLEXICHAT_DATACENTER_ID")
        if env_id is not None:
            try:
                return int(env_id) % 32
            except ValueError:
                pass

        # 2. Try to derive from machine ID or hostname
        import socket

        try:
            hostname = socket.gethostname()
            host_hash = hashlib.sha256(hostname.encode()).digest()
            return host_hash[1] % 32
        except Exception:
            return 1

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def generate(self) -> int:
        with self._lock:
            timestamp = self._get_timestamp()
            if timestamp < self.last_timestamp:
                raise RuntimeError("Clock moved backwards")

            if timestamp == self.last_timestamp:
                self.sequence = (self.sequence + 1) & 0xFFF
                if self.sequence == 0:
                    while timestamp <= self.last_timestamp:
                        timestamp = self._get_timestamp()
            else:
                self.sequence = 0

            self.last_timestamp = timestamp
            return (
                ((timestamp - self.epoch) << 22)
                | (self.datacenter_id << 17)
                | (self.worker_id << 12)
                | self.sequence
            )

    def parse(self, snowflake_id: int) -> Dict[str, int]:
        return {
            "timestamp": (snowflake_id >> 22) + self.epoch,
            "datacenter_id": (snowflake_id >> 17) & 0x1F,
            "worker_id": (snowflake_id >> 12) & 0x1F,
            "sequence": snowflake_id & 0xFFF,
        }


class MessageEncryptor:
    """
    Handles message-at-rest encryption using AES-256-GCM.
    """

    def __init__(self, keyring: Optional[Keyring] = None):
        self.keyring = keyring or Keyring(
            Path.home() / ".plexichat" / "data" / "message_keyring.json",
            env_var="PLEXICHAT_MESSAGE_KEY",
        )

    def encrypt_message(self, content: str, message_id: Optional[int] = None) -> str:
        version, key = self.keyring.get_key()
        aesgcm = AESGCM(key)
        nonce = os.urandom(12)
        aad = str(message_id).encode() if message_id else None
        ciphertext = aesgcm.encrypt(nonce, content.encode("utf-8"), aad)
        combined = nonce + ciphertext
        return f"ENC:{version}:{base64.b64encode(combined).decode('utf-8')}"

    def decrypt_message(
        self, encrypted_content: str, message_id: Optional[int] = None
    ) -> str:
        if not encrypted_content.startswith("ENC:"):
            return encrypted_content
        try:
            parts = encrypted_content.split(":", 2)
            version = int(parts[1])
            _, key = self.keyring.get_key(version)
            aesgcm = AESGCM(key)
            combined = base64.b64decode(parts[2])
            nonce, ciphertext = combined[:12], combined[12:]
            aad = str(message_id).encode() if message_id else None
            return aesgcm.decrypt(nonce, ciphertext, aad).decode("utf-8")
        except Exception:
            raise ValueError("Decryption failed")

    def is_encrypted(self, content: str) -> bool:
        return content.startswith("ENC:")

    def is_key_auto_generated(self) -> bool:
        return self.keyring.current_version == 1


def generate_key_pair() -> Tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization

    private_key = ed25519.Ed25519PrivateKey.generate()
    return (
        private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ),
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )


def sign_data(data: bytes, private_key_bytes: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    return ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes).sign(data)


def verify_signature(data: bytes, signature: bytes, public_key_bytes: bytes) -> bool:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    try:
        ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature, data
        )
        return True
    except Exception:
        return False
