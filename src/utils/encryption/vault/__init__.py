"""
Hardware-rooted vault for master key management.
Supports TPM 2.0 (via tpm2-pytss), HSM (via PKCS#11), and environment-derived KEK.
"""

from __future__ import annotations

import importlib

from .constants import HSM_ENVELOPE_PREFIX
from .errors import HsmCryptoError, VaultPolicyError
from .hardware_vault import HardwareVault
from .hsm_envelope import parse_hsm_envelope as _parse_hsm_envelope
from .machine_key import _load_machine_key_cached, _load_machine_key_impl
from .tpm_kdf import tpm_bound_kek

# Global vault instance for backward compatibility
vault = HardwareVault()

__all__ = [
    "HardwareVault",
    "HsmCryptoError",
    "VaultPolicyError",
    "HSM_ENVELOPE_PREFIX",
    "tpm_bound_kek",
    "vault",
    "_parse_hsm_envelope",
    "_load_machine_key_impl",
    "_load_machine_key_cached",
    "importlib",
]
