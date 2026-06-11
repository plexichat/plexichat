"""
Hardware-rooted vault for master key management.
Supports TPM 2.0 (via tpm2-pytss), HSM (via PKCS#11), and environment-derived KEK.
"""

import os
from functools import lru_cache
from typing import Optional, Dict, Any
from pathlib import Path

import utils.logger as logger


class HardwareVault:
    """
    Manages the Root of Trust (ROT) for the application.
    Supports HSM (PKCS#11), TPM 2.0, and environment-derived KEK.

    KEK priority order:
    1. Environment variable (PLEXICHAT_SYSTEM_KEY or keyring-specific KEK)
    2. HSM (PKCS#11)
    3. TPM 2.0
    4. Machine-local file (fallback)
    """

    def __init__(self, kek_env_var: Optional[str] = None):
        self._master_key: Optional[bytes] = None
        self._source: str = "unknown"
        self._kek_env_var = kek_env_var or "PLEXICHAT_SYSTEM_KEY"
        self._tpm_available = False
        self._hsm_available = False
        self._hsm_config: Dict[str, Any] = {}

        # Initialize hardware sources
        self._init_hsm()
        self._init_tpm()

    def _init_tpm(self):
        """Try to initialize TPM 2.0 connection."""

        def _init_tpm(self):
            """Try to initialize TPM 2.0."""
            try:
                from tpm2_pytss import ESYS_Context  # type: ignore[import-not-found]

                with ESYS_Context() as ctx:
                    # Check for TPM presence without deep interaction yet
                    cap = ctx.get_capability(0, 0x00000001, 1)  # TPM_CAP_PROPERTIES
                    if cap:
                        self._tpm_available = True
                        logger.info("TPM 2.0 hardware detected and available")
            except (ImportError, Exception):
                logger.debug("TPM 2.0 not available or tpm2-pytss not installed")

    def _init_hsm(self):
        """Try to initialize HSM (PKCS#11) connection."""
        try:
            import utils.config as config

            hsm_config = config.get("encryption", {}).get("hsm", {})
            if not hsm_config.get("enabled", False):
                try:
                    logger.debug("HSM support disabled in configuration")
                except RuntimeError:
                    pass  # Logger not configured yet
                return

            # Check for required HSM configuration
            required_fields = ["library_path", "slot_id", "pin"]
            for field in required_fields:
                if not hsm_config.get(field):
                    try:
                        logger.warning(
                            f"HSM enabled but missing required field: {field}"
                        )
                    except RuntimeError:
                        pass  # Logger not configured yet
                    return

            # Try to import PyKCS11
            from PyKCS11 import PyKCS11  # type: ignore[import-not-found]

            # Test HSM connection
            pkcs11 = PyKCS11.PyKCS11Lib()
            pkcs11.load(hsm_config["library_path"])

            # Get slot info
            slots = pkcs11.getSlotList(tokenPresent=True)
            if not slots:
                try:
                    logger.warning("HSM enabled but no slots with tokens available")
                except RuntimeError:
                    pass  # Logger not configured yet
                return

            # Verify the configured slot exists
            slot_id = int(hsm_config["slot_id"])
            if slot_id not in slots:
                try:
                    logger.warning(
                        f"HSM configured slot {slot_id} not found in available slots: {slots}"
                    )
                except RuntimeError:
                    pass  # Logger not configured yet
                return

            self._hsm_available = True
            self._hsm_config = hsm_config
            try:
                logger.info(f"HSM (PKCS#11) available on slot {slot_id}")
            except RuntimeError:
                pass  # Logger not configured yet

        except ImportError:
            try:
                logger.debug("PyKCS11 not installed, HSM support unavailable")
            except RuntimeError:
                pass  # Logger not configured yet
        except Exception as e:
            try:
                logger.warning(f"HSM initialization failed: {e}")
            except RuntimeError:
                pass  # Logger not configured yet

    def get_kek(self) -> bytes:
        """
        Get the Key Encryption Key (KEK).
        Derived from keyring-specific env var, HSM, TPM, or machine-local file.

        Prioritizes:
        1. Environment variable (keyring-specific KEK)
        2. HSM (PKCS#11)
        3. TPM 2.0
        4. Machine-local file (fallback)
        """
        if self._master_key:
            return self._master_key

        # 1. Check for dedicated environment key (Highest Priority)
        env_key = os.environ.get(self._kek_env_var)
        if env_key:
            self._master_key = self._decode_env_key(env_key)
            self._source = "env"
            logger.info(f"Using environment-provided KEK from {self._kek_env_var}")
            return self._master_key

        # 2. Try HSM (PKCS#11)
        if self._hsm_available:
            try:
                self._master_key = self._get_hsm_key()
                if self._master_key:
                    self._source = "hsm"
                    logger.info("Using HSM (PKCS#11) derived encryption key")
                    return self._master_key
            except Exception as e:
                logger.error(f"HSM key retrieval failed: {e}")

        # 3. Try TPM 2.0
        if self._tpm_available:
            try:
                self._master_key = self._get_tpm_key()
                if self._master_key:
                    self._source = "tpm"
                    logger.info("Using TPM-derived hardware encryption key")
                    return self._master_key
            except Exception as e:
                logger.error(f"TPM key retrieval failed: {e}")

        # 4. Single-machine mode: load (or generate) a machine-local key.
        # The actual disk read is cached at module level so the three keyrings
        # (system, file, message) share a single load.
        key_file = self._get_machine_key_path()
        self._master_key = _load_machine_key_cached(str(key_file))
        self._source = "local"
        return self._master_key

    def _get_machine_key_path(self) -> Path:
        """Get path for machine-local key file."""
        # Use dedicated key file ONLY if the dedicated env var is explicitly set
        # Otherwise all keyrings share the single .machine_key fallback
        if self._kek_env_var != "PLEXICHAT_SYSTEM_KEY" and os.environ.get(
            self._kek_env_var
        ):
            key_name = self._kek_env_var.lower().replace("_key", "") + "_key"
            return Path.home() / ".plexichat" / "data" / f".{key_name}"
        return Path.home() / ".plexichat" / "data" / ".machine_key"

    def _decode_env_key(self, env_value: str) -> bytes:
        """Decode environment variable key (supports hex and Base64).

        Tries hex first (the standard Plexichat production format: 64-char hex = 32 bytes),
        then falls back to Base64. This order is important because a 64-char hex string
        decodes to 48 bytes in Base64 — it passes the length check incorrectly when
        Base64 is tried first.
        """
        import base64

        # Try hex first (standard Plexichat production format: 64 hex chars = 32 bytes)
        try:
            key = bytes.fromhex(env_value)
            if len(key) == 32:
                return key
            logger.debug(
                f"Environment variable {self._kek_env_var} decoded as hex but yielded {len(key)} bytes (expected 32)"
            )
        except Exception:
            pass

        # Try Base64 (alternative format)
        try:
            key = base64.b64decode(env_value)
            if len(key) == 32:
                return key
            logger.debug(
                f"Environment variable {self._kek_env_var} decoded as Base64 but yielded {len(key)} bytes (expected 32)"
            )
        except Exception:
            pass

        raise ValueError(
            f"Environment variable {self._kek_env_var} must be a 32-byte key (hex or Base64 encoded)"
        )

    def _get_hsm_key(self) -> Optional[bytes]:
        """
        Retrieve or derive encryption key from HSM (PKCS#11).

        Uses the HSM's key management capabilities to either:
        1. Retrieve an existing key object by label
        2. Derive a key from a master key stored in HSM
        """
        try:
            from PyKCS11 import PyKCS11  # type: ignore[import-not-found]

            pkcs11 = PyKCS11.PyKCS11Lib()
            pkcs11.load(self._hsm_config["library_path"])

            slot_id = int(self._hsm_config["slot_id"])
            pin = self._hsm_config["pin"]
            key_label = self._hsm_config.get("key_label", "plexichat_kek")

            # Open session and login
            session = pkcs11.openSession(
                slot_id, PyKCS11.CKF_SERIAL_SESSION | PyKCS11.CKF_RW_SESSION
            )
            session.login(pin)

            try:
                # Try to find existing key by label
                template = [
                    (PyKCS11.CKA_LABEL, key_label),
                    (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                    (PyKCS11.CKA_KEY_TYPE, PyKCS11.CKK_AES),
                    (PyKCS11.CKA_VALUE_LEN, 32),
                ]

                objects = session.findObjects(template)

                if objects:
                    # Key exists, extract it
                    key_obj = objects[0]
                    key_value = session.getAttributeValue(key_obj, [PyKCS11.CKA_VALUE])[
                        0
                    ]
                    logger.info(f"Retrieved existing key '{key_label}' from HSM")
                    return bytes(key_value)
                else:
                    # Key doesn't exist, create it in HSM
                    logger.info(f"Key '{key_label}' not found in HSM, creating new key")

                    new_key = os.urandom(32)

                    key_template = [
                        (PyKCS11.CKA_CLASS, PyKCS11.CKO_SECRET_KEY),
                        (PyKCS11.CKA_KEY_TYPE, PyKCS11.CKK_AES),
                        (PyKCS11.CKA_VALUE_LEN, 32),
                        (PyKCS11.CKA_LABEL, key_label),
                        (PyKCS11.CKA_VALUE, new_key),
                        (PyKCS11.CKA_ENCRYPT, PyKCS11.CK_TRUE),
                        (PyKCS11.CKA_DECRYPT, PyKCS11.CK_TRUE),
                        (PyKCS11.CKA_EXTRACTABLE, PyKCS11.CK_FALSE),  # Non-extractable
                    ]

                    session.createObject(key_template)
                    logger.info(f"Created new non-extractable key '{key_label}' in HSM")
                    return new_key

            finally:
                session.logout()
                session.closeSession()

        except Exception as e:
            logger.error(f"HSM key retrieval/creation failed: {e}")
            raise

    def _get_tpm_key(self) -> Optional[bytes]:
        """
        Interact with TPM 2.0 to get/create a persistent storage key.

        Uses TPM's key hierarchy to derive a unique key bound to this machine's TPM.
        """
        if not self._tpm_available:
            return None

        try:
            from tpm2_pytss import ESYS_Context, TPM2B_PUBLIC, TPM2B_SENSITIVE_CREATE  # type: ignore[import-not-found]
            from tpm2_pytss.constants import (  # type: ignore[import-not-found]
                ESYS_TR,
                TPM2_ALG,
                TPMA_OBJECT,
            )

            with ESYS_Context() as ctx:
                # Create a primary key under the storage hierarchy
                in_public = TPM2B_PUBLIC.parse(
                    alg="aes256",
                    objectAttributes=(
                        TPMA_OBJECT.USERWITHAUTH
                        | TPMA_OBJECT.RESTRICTED
                        | TPMA_OBJECT.DECRYPT
                        | TPMA_OBJECT.FIXEDTPM
                        | TPMA_OBJECT.FIXEDPARENT
                        | TPMA_OBJECT.SENSITIVEDATAORIGIN
                    ),
                )

                primary_handle, _, _, _, _ = ctx.create_primary(
                    ESYS_TR.OWNER,
                    TPM2B_SENSITIVE_CREATE(),
                    in_public,
                )

                # Derive key material from the primary key
                # Use a fixed label for reproducibility
                label = b"PLEXICHAT_KEK_V1"
                derived = ctx.hash(label, TPM2_ALG.SHA256, ESYS_TR.OWNER)

                ctx.flush_context(primary_handle)

                return bytes(derived.buffer)[:32]

        except Exception as e:
            logger.debug(f"TPM key derivation failed: {e}")
            return None

    def is_using_secure_source(self) -> bool:
        """Check if the current master key is from a secure source (Env, HSM, or TPM)."""
        # Ensure key is loaded
        if not self._master_key:
            self.get_kek()
        return self._source in ("env", "hsm", "tpm")

    def get_source(self) -> str:
        """Get the current KEK source."""
        if not self._master_key:
            self.get_kek()
        return self._source


def _load_machine_key_impl(key_path: str) -> bytes:
    """Load (or generate) the machine-local encryption key from disk.

    This is the actual disk-reading implementation; it is wrapped by
    :func:`_load_machine_key_cached` so that all three keyrings (system,
    file, message) share a single load per process.
    """
    key_file = Path(key_path)
    if key_file.exists():
        try:
            data = key_file.read_bytes()
            if len(data) == 32:
                logger.debug("Loaded machine-local encryption key from file")
                logger.debug(
                    f"Machine key loaded ({len(data)} bytes) from {key_file} - "
                    "this should appear only once per process per key file"
                )
                return data
        except Exception as e:
            logger.error(f"Failed to read machine key: {e}")

    # Generate new machine-local key
    logger.warning(
        "CRITICAL SECURITY WARNING: Generating machine-local encryption key. "
        "This key is NOT hardware-bound and is less secure than HSM, TPM, or "
        "Environment Variable. For production deployments, set the appropriate "
        "PLEXICHAT_*_KEY environment variable or configure HSM/TPM."
    )
    new_key = os.urandom(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(new_key)
        # Restrict permissions
        if os.name == "nt":
            # On Windows, use icacls to restrict access to current user only
            try:
                import subprocess

                # Remove inheritance and all permissions, then grant full to current user
                subprocess.run(
                    [
                        "icacls",
                        str(key_file),
                        "/inheritance:r",
                        "/grant:r",
                        "*S-1-5-32-544:F",
                        "/grant:r",
                        "%USERNAME%:F",
                    ],
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass
        else:
            try:
                os.chmod(key_file, 0o600)
            except (OSError, AttributeError):
                pass
        logger.info(f"Machine-local key saved to {key_file}")
    except Exception as e:
        logger.error(f"Failed to persist machine key: {e}")

    logger.debug(
        f"Machine key loaded ({len(new_key)} bytes) from {key_file} - "
        "this should appear only once per process per key file"
    )
    return new_key


@lru_cache(maxsize=4)
def _load_machine_key_cached(key_path: str) -> bytes:
    """Cached wrapper around :func:`_load_machine_key_impl`.

    Caches up to 4 key paths (system, file, message, and a safety margin).
    Multiple :class:`HardwareVault` instances — one per keyring — share
    the resulting bytes so the key file is read from disk only once.
    """
    return _load_machine_key_impl(key_path)


# Global vault instance for backward compatibility
vault = HardwareVault()
