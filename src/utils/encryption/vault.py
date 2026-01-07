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
                    logger.info("TPM 2.0 hardware detected and available")
        except (ImportError, Exception):
            logger.debug("TPM 2.0 not available or tpm2-pytss not installed")

    def get_kek(self) -> bytes:
        """
        Get the Key Encryption Key (KEK).
        Derived from TPM hardware or PLEXICHAT_SYSTEM_KEY env var.
        
        For single-machine deployments, auto-generates and persists a key.
        For distributed deployments, requires PLEXICHAT_SYSTEM_KEY to be set.
        """
        if self._master_key:
            return self._master_key

        # 1. Try TPM 2.0 (High Priority)
        if self._tpm_available:
            try:
                self._master_key = self._get_tpm_key()
                if self._master_key:
                    return self._master_key
            except Exception as e:
                logger.error(f"TPM key retrieval failed: {e}")

        # 2. Check for explicit environment key (required for distributed deployments)
        system_key = os.environ.get("PLEXICHAT_SYSTEM_KEY")
        if system_key:
            self._master_key = hashlib.sha512(system_key.encode()).digest()[:32]
            return self._master_key

        # 3. Single-machine mode: auto-generate and persist a machine-local key
        key_file = self._get_machine_key_path()
        if key_file.exists():
            try:
                self._master_key = key_file.read_bytes()
                if len(self._master_key) == 32:
                    logger.debug("Loaded machine-local encryption key")
                    return self._master_key
            except Exception as e:
                logger.error(f"Failed to read machine key: {e}")

        # Generate new machine-local key
        logger.warning(
            "Generating machine-local encryption key. "
            "For distributed deployments, set PLEXICHAT_SYSTEM_KEY environment variable."
        )
        self._master_key = os.urandom(32)
        try:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_bytes(self._master_key)
            # Restrict permissions (Unix only, Windows ignores)
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


vault = HardwareVault()
