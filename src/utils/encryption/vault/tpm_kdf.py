"""TPM-bound KEK derivation."""

from __future__ import annotations


def tpm_bound_kek(
    public_bytes: bytes,
    name_bytes: bytes,
    *,
    label: str = "PLEXICHAT_KEK_V2",
    salt: str = "plexichat:tpm-kek:v2",
) -> bytes:
    """Derive a TPM-bound 32-byte KEK via HKDF.

    Binds the output to the TPM's primary public area + object name.
    Pure function of its inputs.
    """
    from ..hkdf import derive_key

    if not public_bytes or not name_bytes:
        raise ValueError("tpm_bound_kek needs non-empty public_bytes and name_bytes")
    if not isinstance(label, str) or not label:
        raise ValueError("tpm_bound_kek label must be a non-empty string")
    if not isinstance(salt, str) or not salt:
        raise ValueError("tpm_bound_kek salt must be a non-empty string")
    return derive_key(
        public_bytes + name_bytes,
        salt=salt.encode("utf-8"),
        info=label.encode("utf-8"),
        length=32,
    )
