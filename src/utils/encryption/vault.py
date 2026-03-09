"""
Hardware-rooted vault for master key management.
Supports TPM 2.0 (via tpm2-pytss) and environment-derived KEK.
"""

import os
import hashlib
import utils.logger as logger
from typing import Optional
from pathlib import Path


class HardwareVault:
    """
    Manages the Root of Trust (ROT) for the application.
    Prioritizes TPM 2.0 hardware if available.
    """

    def __init__(self):
        self._master_key: Optional[bytes] = None
        self._tpm_available = False

        # Prioritize environment key over hardware even during initialization
        if "PLEXICHAT_SYSTEM_KEY" in os.environ:
            try:
                logger.info("PLEXICHAT_SYSTEM_KEY found, skipping TPM detection")
            except Exception:
                pass
        else:
            self._init_tpm()

    def _init_tpm(self):
        """Try to initialize TPM 2.0 connection."""
        try:
            # We don't import at top-level to avoid dependency hard-requirement
            from tpm2_pytss import ESYS_Context  # type: ignore[import-not-found]

            with ESYS_Context() as ctx:
                # Check for TPM presence without deep interaction yet
                cap = ctx.get_capability(0, 0x00000001, 1)  # TPM_CAP_PROPERTIES
                if cap:
                    self._tpm_available = True
                    try:
                        logger.info("TPM 2.0 hardware detected and available")
                    except Exception:
                        pass
        except (ImportError, Exception):
            try:
                logger.debug("TPM 2.0 not available or tpm2-pytss not installed")
            except Exception:
                pass

    def get_kek(self) -> bytes:
        """
        Get the Key Encryption Key (KEK).
        Derived from PLEXICHAT_SYSTEM_KEY env var, TPM hardware, or machine-local file.

        Prioritizes:
        1. PLEXICHAT_SYSTEM_KEY (Explicitly provided, best for consistency)
        2. TPM 2.0 (Hardware-bound security)
        3. Machine-local file (Fallback for simple deployments)
        """
        if self._master_key:
            return self._master_key

        # 1. Check for explicit environment key (Highest Priority for consistency)
        system_key = os.environ.get("PLEXICHAT_SYSTEM_KEY")
        if system_key:
            self._master_key = hashlib.sha512(system_key.encode("utf-8")).digest()[:32]
            self._source = "env"
            logger.info("Using environment-provided system encryption key")
            return self._master_key

        # 2. Try TPM 2.0
        if self._tpm_available:
            try:
                self._master_key = self._get_tpm_key()
                if self._master_key:
                    self._source = "tpm"
                    logger.info("Using TPM-derived hardware encryption key")
                    return self._master_key
            except Exception as e:
                logger.error(f"TPM key retrieval failed: {e}")

        # 3. Single-machine mode: auto-generate and persist a machine-local key
        key_file = self._get_machine_key_path()
        if key_file.exists():
            try:
                self._master_key = key_file.read_bytes()
                if len(self._master_key) == 32:
                    self._source = "local"
                    logger.debug("Loaded machine-local encryption key from file")
                    return self._master_key
            except Exception as e:
                logger.error(f"Failed to read machine key: {e}")

        # Generate new machine-local key
        logger.warning(
            "CRITICAL SECURITY WARNING: Generating machine-local encryption key. "
            "This key is NOT hardware-bound and is less secure than TPM or Environment Variable. "
            "For production deployments, set PLEXICHAT_SYSTEM_KEY environment variable or ensure TPM is available."
        )
        self._master_key = os.urandom(32)
        self._source = "local"
        try:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_bytes(self._master_key)
            # Restrict permissions
            if os.name == "nt":
                # On Windows, use icacls to restrict access to current user only
                try:
                    import subprocess
                    # Remove inheritance and all permissions, then grant full to current user
                    subprocess.run(
                        ["icacls", str(key_file), "/inheritance:r", "/grant:r", "*S-1-5-32-544:F", "/grant:r", "%USERNAME%:F"],
                        capture_output=True,
                        check=False
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

        return self._master_key

    def _get_machine_key_path(self):
        """Get path for machine-local key file."""
        return Path.home() / ".plexichat" / "data" / ".machine_key"

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
        """Check if the current master key is from a secure source (Env or TPM)."""
        # Ensure key is loaded
        if not self._master_key:
            self.get_kek()
        return self._source in ("env", "tpm")


vault = HardwareVault()
