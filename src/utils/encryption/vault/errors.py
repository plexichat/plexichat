"""Vault exceptions."""

from __future__ import annotations


class HsmCryptoError(RuntimeError):
    """Raised when HSM-managed crypto (non-extractable key ops) fails."""


class VaultPolicyError(RuntimeError):
    """Raised when hardware-root-of-trust policy cannot be satisfied."""
