"""Vault constants and defaults."""

from __future__ import annotations

from pathlib import Path

HSM_ENVELOPE_PREFIX = "HSM1:"
HSM_IV_BYTES = 12
HSM_TAG_BYTES = 16
HSM_MIN_ENVELOPE_BYTES = HSM_IV_BYTES + HSM_TAG_BYTES
# Cap decoded HSM envelope to avoid memory abuse on malformed input.
HSM_MAX_ENVELOPE_BYTES = 16 * 1024 * 1024

KEK_VALID_LENGTHS = (32, 48, 64)
AES256_KEK_BYTES = 32

TPM_DEFAULT_LABEL = "PLEXICHAT_KEK_V2"
TPM_DEFAULT_SALT = "plexichat:tpm-kek:v2"
TPM_DEFAULT_HIERARCHY = "OWNER"
TPM_DEFAULT_TEMPLATE = "aes256"

HARDWARE_REQUIRED_MODES = frozenset({"require", "required", "hardware", "tpm", "hsm"})

MACHINE_KEY_DIR = Path.home() / ".plexichat" / "data"
MACHINE_KEY_FILENAME = ".machine_key"
